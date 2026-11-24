"""Regression tests for the Gabsweb CashFlow production PWA.

These tests cover the server endpoints, manifest and icon configuration, the
service-worker security classification and the template wiring. Browser-only
behaviour (installation prompts and operating-system splash screens) is covered
by the JavaScript regression suite and remains subject to manual device checks.
"""

import json
import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from PIL import Image, ImageChops

BASE_DIR = Path(settings.BASE_DIR)
STATIC_ASSETS = BASE_DIR / "static" / "assets"


def read_static(relative_path):
    return (BASE_DIR / "static" / relative_path).read_text(encoding="utf-8")


def manifest_bytes(response):
    return b"".join(response.streaming_content)


def read_template(relative_path):
    return (BASE_DIR / "core" / "templates" / relative_path).read_text(
        encoding="utf-8"
    )


class ManifestEndpointTests(TestCase):
    def test_manifest_is_served_from_the_site_root(self):
        response = self.client.get(reverse("pwa_manifest"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["Content-Type"].startswith(
                "application/manifest+json"
            )
        )
        self.assertEqual(
            response.headers["Cache-Control"],
            "no-cache, no-store, must-revalidate",
        )
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_manifest_is_valid_and_scoped_to_the_application(self):
        response = self.client.get(reverse("pwa_manifest"))
        manifest = json.loads(manifest_bytes(response))

        self.assertEqual(manifest["name"], "Gabsweb CashFlow")
        self.assertEqual(manifest["short_name"], "CashFlow")
        self.assertEqual(manifest["id"], "/")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["lang"], "en")
        self.assertEqual(manifest["theme_color"].lower(), "#13151c")
        self.assertEqual(manifest["background_color"].lower(), "#0c0d11")
        self.assertTrue(manifest["description"])
        self.assertFalse(manifest["prefer_related_applications"])
        self.assertIn(
            {
                "platform": "webapp",
                "url": "/manifest.webmanifest",
                "id": "/",
            },
            manifest["related_applications"],
        )

    def test_manifest_declares_standard_and_maskable_icons(self):
        manifest = json.loads(manifest_bytes(self.client.get(reverse("pwa_manifest"))))
        icons = manifest["icons"]

        indexed = {}
        for icon in icons:
            sizes = icon["sizes"]
            purpose = icon.get("purpose", "any")
            indexed[(sizes, purpose)] = icon

        # Chromium only offers beforeinstallprompt when the manifest exposes
        # installable "any" artwork at the standard 192px and 512px sizes.
        self.assertIn(("192x192", "any"), indexed)
        self.assertIn(("512x512", "any"), indexed)
        self.assertIn(("1254x1254", "maskable"), indexed)

    def test_every_manifest_icon_resolves_to_a_real_image(self):
        manifest = json.loads(manifest_bytes(self.client.get(reverse("pwa_manifest"))))

        for icon in manifest["icons"]:
            src = icon["src"]
            self.assertTrue(src.startswith("/static/"))
            # A version query is required so an installed WebAPK treats the
            # icons as new and refreshes its launcher/splash artwork.
            path = src.split("?", 1)[0]

            found = finders.find(path[len("/static/"):])
            self.assertIsNotNone(found, f"Missing manifest icon: {src}")

            expected = tuple(
                int(value) for value in icon["sizes"].split("x", 1)
            )
            with Image.open(found) as image:
                self.assertEqual(image.size, expected)

    def test_manifest_shortcuts_target_existing_application_routes(self):
        manifest = json.loads(manifest_bytes(self.client.get(reverse("pwa_manifest"))))

        for shortcut in manifest["shortcuts"]:
            self.assertTrue(shortcut["url"].startswith("/"))
            self.assertTrue(shortcut["name"])
            # Every shortcut must resolve through Django, not just look like a
            # path. A launch shortcut that 404s is a broken installation.
            self.assertIsNotNone(resolve(shortcut["url"]))

        self.assertIsNotNone(resolve(manifest["start_url"]))


class IconAssetTests(TestCase):
    ICON = "assets/icons/gabsweb_PWA_Maskable_Icon.png"

    def test_approved_icon_is_used_unmodified(self):
        found = finders.find(self.ICON)
        self.assertIsNotNone(found)
        with Image.open(found) as image:
            self.assertEqual(image.size, (1254, 1254))
            self.assertEqual(image.mode, "RGB")

    def test_approved_icon_keeps_an_opaque_canvas(self):
        found = finders.find(self.ICON)
        with Image.open(found) as image:
            self.assertEqual(image.mode, "RGB")
            width, height = image.size
            for point in (
                (0, 0),
                (width - 1, 0),
                (0, height - 1),
                (width - 1, height - 1),
            ):
                self.assertNotEqual(image.getpixel(point), (0, 0, 0))

    def test_approved_icon_artwork_stays_inside_the_safe_zone(self):
        # Android crops maskable icons to the central 80% safe zone, so the
        # gradient disc must remain inside that area on every launcher shape.
        found = finders.find(self.ICON)
        with Image.open(found) as image:
            width, height = image.size
            pixels = image.load()
            xs = []
            ys = []
            for y in range(height):
                for x in range(width):
                    r, g, b = pixels[x, y]
                    if max(r, g, b) - min(r, g, b) > 40 and max(r, g, b) > 80:
                        xs.append(x)
                        ys.append(y)
            self.assertTrue(xs, "The icon must keep the gradient disc.")
            disc_ratio = (max(xs) - min(xs) + 1) / width
            self.assertGreaterEqual(disc_ratio, 0.70)
            self.assertLessEqual(disc_ratio, 0.80)
            self.assertGreaterEqual(min(xs) / width, 0.05)
            self.assertLessEqual(max(xs) / width, 0.95)
            self.assertGreaterEqual(min(ys) / height, 0.05)
            self.assertLessEqual(max(ys) / height, 0.95)

    def test_approved_icon_has_the_white_mark(self):
        found = finders.find(self.ICON)
        with Image.open(found) as image:
            width, height = image.size
            pixels = image.load()
            white_points = [
                (x, y)
                for y in range(height)
                for x in range(width)
                if all(channel >= 235 for channel in pixels[x, y])
            ]
            self.assertTrue(white_points, "The icon must keep the white mark.")
            xs = [point[0] for point in white_points]
            ratio = (max(xs) - min(xs) + 1) / width
            self.assertGreaterEqual(ratio, 0.45)
            self.assertLessEqual(ratio, 0.70)

    def test_manifest_references_only_the_approved_icon_artwork(self):
        manifest = json.loads(
            manifest_bytes(self.client.get(reverse("pwa_manifest")))
        )
        sources = {
            icon["src"].split("?", 1)[0] for icon in manifest["icons"]
        }
        self.assertEqual(
            sources,
            {
                f"/static/{self.ICON}",
                "/static/assets/icons/gabsweb_PWA_Icon_192x192.png",
                "/static/assets/icons/gabsweb_PWA_Icon_512x512.png",
            },
        )
        maskable = {
            icon["src"].split("?", 1)[0]
            for icon in manifest["icons"]
            if icon.get("purpose") == "maskable"
        }
        self.assertEqual(maskable, {f"/static/{self.ICON}"})


class ServiceWorkerEndpointTests(TestCase):
    def test_service_worker_is_served_from_the_site_root(self):
        response = self.client.get(reverse("pwa_service_worker"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["Content-Type"].startswith("application/javascript")
        )
        self.assertEqual(response.headers["Service-Worker-Allowed"], "/")
        self.assertEqual(
            response.headers["Cache-Control"],
            "no-cache, no-store, must-revalidate",
        )
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_service_worker_never_caches_html_or_private_data(self):
        source = read_static("assets/js/service-worker.js")

        self.assertIn('request.mode === "navigate"', source)
        self.assertIn("networkWithOfflineFallback", source)
        self.assertIn('url.pathname.startsWith("/static/")', source)
        self.assertIn("if (!isStaticAsset(url)) return;", source)
        self.assertIn("no-store|private", source)
        self.assertIn("No financial data is stored on this device.", read_static("assets/offline.html"))

        # Private application routes must never appear in a caching branch.
        for private_path in (
            "/transactions",
            "/budgets-api",
            "/budget-api",
            "/savings-api",
            "/goals-api",
            "/insights-api",
            "/notifications-api",
            "/profile",
            "/accounts",
            "/login",
            "/password-reset",
        ):
            self.assertNotIn(
                f'startsWith("{private_path}")',
                source,
                f"Service worker must not special-case {private_path}",
            )

    def test_service_worker_has_versioned_caches_and_cleanup(self):
        source = read_static("assets/js/service-worker.js")

        self.assertRegex(source, r'CACHE_VERSION = "\d{4}-\d{2}-\d{2}\.\d+"')
        self.assertIn("caches.delete", source)
        self.assertIn("key.startsWith(CACHE_PREFIX)", source)
        self.assertIn("gabsweb-", source)

    def test_service_worker_skips_waiting_only_for_updates(self):
        source = read_static("assets/js/service-worker.js")

        self.assertIn("self.skipWaiting()", source)
        self.assertIn('event.data.type !== "SKIP_WAITING"', source)
        self.assertIn("if (!self.registration.active) return self.skipWaiting();", source)

    def test_offline_page_is_branded_and_does_not_fabricate_financial_data(self):
        offline = read_static("assets/offline.html")

        self.assertIn("You are offline", offline)
        self.assertIn("Gabsweb CashFlow", offline)
        self.assertIn("No financial data is stored on this device.", offline)
        self.assertIn("window.location.reload()", offline)
        self.assertIn('<meta name="theme-color" content="#0c0d11" />', offline)
        self.assertNotIn('name="color-scheme"', offline)
        self.assertNotIn("NGN", offline)
        self.assertNotIn("₦", offline)

    def test_offline_page_is_precached(self):
        source = read_static("assets/js/service-worker.js")

        self.assertIn('const OFFLINE_URL = "/static/assets/offline.html";', source)
        self.assertRegex(
            source,
            r"const PRECACHE_URLS = \[\s*OFFLINE_URL",
        )


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class PwaTemplateIntegrationTests(TestCase):
    def test_base_layout_wires_manifest_icons_and_pwa_assets(self):
        base = read_template("layouts/base.html")

        self.assertIn('rel="manifest"', base)
        self.assertIn("/manifest.webmanifest", base)
        self.assertIn("apple-touch-icon", base)
        self.assertIn("gabsweb_PWA_Maskable_Icon.png", base)
        self.assertIn("assets/js/pwa.js", base)
        self.assertIn("assets/js/theme-color.js", base)
        self.assertIn('<meta name="theme-color" content="#0c0d11" />', base)
        self.assertNotIn('name="color-scheme"', base)
        self.assertIn("assets/css/pwa.css", base)
        self.assertIn('include "partials/pwa_update.html"', base)

    def test_theme_colour_initialises_before_blocking_assets(self):
        base = read_template("layouts/base.html")

        theme_scripts = base.index("{% block theme_scripts %}")
        theme_colour = base.index("assets/js/theme-color.js")
        lucide = base.index("unpkg.com/lucide")
        stylesheet = base.index("assets/css/index.css")

        self.assertLess(theme_scripts, theme_colour)
        self.assertLess(theme_colour, lucide)
        self.assertLess(theme_colour, stylesheet)
        self.assertEqual(base.count("assets/js/theme-color.js"), 1)

    def test_shared_app_header_and_bottom_navigation_theme_surfaces(self):
        index_css = read_static("assets/css/index.css")

        header = re.search(
            r"\[data-page\]\s+\.app-header\s*\{([^}]*)\}",
            index_css,
        )
        self.assertIsNotNone(header)
        self.assertIn("border-top: 0", header.group(1))
        self.assertIn("background: var(--bg)", header.group(1))
        self.assertNotIn('[data-page="Dashboard"] .app-header', index_css)
        safe_area = re.search(
            r"nav\.bottom-nav\s*\{([^}]*)\}", index_css
        )
        self.assertIsNotNone(safe_area)
        self.assertIn("padding-bottom: env(safe-area-inset-bottom, 0px)", safe_area.group(1))
        self.assertIn("background-color: var(--panel)", safe_area.group(1))

    def test_every_authenticated_page_uses_the_shared_app_header(self):
        from .models import User

        user = User.objects.create_user(
            email="header-owner@example.com", password="StrongPass123!"
        )
        self.client.force_login(user)
        routes = [
            "core:index",
            "core:transaction",
            "core:budgets",
            "core:goals",
            "core:insights",
            "core:savings",
            "core:settings",
            "core:planner",
            "core:work_schedule",
        ]

        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                html = response.content.decode("utf-8")
                self.assertEqual(
                    html.count('<header class="app-header'),
                    1,
                    f"{route} must use exactly one shared app header",
                )
                self.assertRegex(
                    html,
                    r'<body[^>]*data-page="[^"]+"',
                    f"{route} must identify itself as an authenticated app page",
                )

    def test_app_shell_includes_one_installation_dialog_only_when_authenticated(self):
        app_base = read_template("layouts/app_base.html")
        install = read_template("partials/pwa_install.html")

        self.assertEqual(
            app_base.count('include "partials/pwa_install.html"'),
            1,
            "The app shell must include exactly one installation dialog",
        )
        self.assertEqual(
            install.count('id="pwa-install-dialog"'),
            1,
            "The installation partial must define exactly one dialog",
        )
        self.assertIn('id="pwa-install-confirm"', install)
        self.assertIn('id="pwa-install-dismiss"', install)
        self.assertIn('id="pwa-install-later"', install)
        self.assertEqual(install.count("data-pwa-close"), 2)
        self.assertNotIn('id="pwa-dont-ask"', install)

        # The Install app button must be rendered without a hidden attribute.
        # A hidden button previously let the close control occupy its place.
        confirm = re.search(
            r'<button[^>]*id="pwa-install-confirm"[^>]*>', install
        )
        self.assertIsNotNone(confirm)
        self.assertNotRegex(confirm.group(0), r"\bhidden\b")
        self.assertIn('type="button"', confirm.group(0))
        self.assertIn("data-pwa-install", confirm.group(0))

    def test_update_notice_is_available_on_every_layout(self):
        update = read_template("partials/pwa_update.html")

        self.assertIn('id="pwa-update-banner"', update)
        self.assertIn('id="pwa-update-action"', update)
        self.assertIn('id="pwa-update-dismiss"', update)
        self.assertIn('aria-live="polite"', update)

    def test_public_login_page_does_not_show_installation_onboarding(self):
        response = self.client.get(reverse("core:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/manifest.webmanifest")
        self.assertContains(response, "assets/js/pwa.js")
        self.assertNotContains(response, 'id="pwa-install-dialog"')

    def test_authenticated_shell_renders_install_and_update_controls(self):
        from .models import User

        user = User.objects.create_user(
            email="pwa-owner@example.com", password="StrongPass123!"
        )
        self.client.force_login(user)

        response = self.client.get(reverse("core:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="pwa-install-dialog"')
        self.assertContains(response, 'id="pwa-update-banner"')
        self.assertContains(response, "/manifest.webmanifest")

    def test_authenticated_shell_renders_exactly_one_install_dialog(self):
        from .models import User

        user = User.objects.create_user(
            email="pwa-single-dialog@example.com", password="StrongPass123!"
        )
        self.client.force_login(user)

        html = self.client.get(reverse("core:index")).content.decode("utf-8")

        self.assertEqual(html.count('id="pwa-install-dialog"'), 1)
        self.assertEqual(html.count('id="pwa-install-confirm"'), 1)
        self.assertEqual(html.count('id="pwa-install-dismiss"'), 1)
        self.assertEqual(html.count('id="pwa-install-later"'), 1)

    def test_manifest_scope_is_not_broken_by_authentication_redirects(self):
        response = self.client.get(reverse("core:index"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:login"), response["Location"])

    def test_install_copy_is_platform_specific(self):
        source = read_static("assets/js/pwa.js")

        self.assertIn('platform === "ios"', source)
        self.assertIn("Add to Home Screen", source)
        self.assertIn('platform === "safari"', source)
        self.assertIn("Add to Dock", source)
        self.assertIn('platform === "samsung"', source)
        self.assertIn('platform === "firefox"', source)
        self.assertIn("beforeinstallprompt", source)
        self.assertIn("appinstalled", source)
        self.assertIn("promptEvent.prompt()", source)

    def test_beforeinstallprompt_is_captured_before_the_pwa_module(self):
        base = read_template("layouts/base.html")
        app_base = read_template("layouts/app_base.html")
        source = read_static("assets/js/pwa.js")

        self.assertIn("beforeinstallprompt", base)
        self.assertIn("__gabswebDeferredInstallPrompt", base)
        self.assertEqual(
            base.count("beforeinstallprompt"),
            1,
            "Only the early capture script may listen for beforeinstallprompt",
        )
        self.assertNotIn(
            "beforeinstallprompt",
            app_base,
            "The event must be captured once in base.html, not per layout",
        )
        self.assertIn("__gabswebHandleInstallPrompt", base)
        self.assertIn("__gabswebHandleInstallPrompt", source)
        self.assertNotIn(
            'window.addEventListener("beforeinstallprompt"',
            source,
            "pwa.js must not register a second beforeinstallprompt listener",
        )
        self.assertLess(
            base.index("__gabswebDeferredInstallPrompt"),
            base.index("assets/js/pwa.js"),
        )

    def test_install_button_uses_the_retained_prompt_once(self):
        source = read_static("assets/js/pwa.js")

        self.assertIn("var promptEvent = getDeferredPrompt();", source)
        self.assertIn("promptEvent.prompt()", source)
        self.assertIn("promptEvent.userChoice", source)
        self.assertIn("clearDeferredPrompt();", source)
        self.assertNotIn("var deferredPrompt", source)

    def test_install_dialog_fits_mobile_viewports(self):
        css = read_static("assets/css/pwa.css")

        self.assertIn("max-height: calc(100dvh - 2rem)", css)
        self.assertIn("@media (max-width: 480px)", css)
        self.assertIn("max-height: calc(100dvh - 10px)", css)
        self.assertIn("min-height: 44px", css)

    def test_install_onboarding_is_login_scoped(self):
        source = read_static("assets/js/pwa.js")
        base = read_template("layouts/app_base.html")

        self.assertNotIn("DISMISS_WINDOW_DAYS", source)
        self.assertNotIn("MAX_AUTO_DISMISSALS", source)
        self.assertNotIn("state.never", source)
        self.assertIn("isLoginPrompt", source)
        self.assertIn("gabsweb_pwa_prompt", (BASE_DIR / "core" / "signals.py").read_text(encoding="utf-8"))
        self.assertIn("data-pwa-login-prompt", base)
        self.assertIn("isStandalone", source)
        self.assertIn("display-mode: standalone", source)

    def test_normal_login_prompts_once_and_refresh_does_not_repeat(self):
        from .models import User

        User.objects.create_user(
            email="pwa-login@example.com", password="StrongPass123!"
        )
        response = self.client.post(
            reverse("core:login"),
            {"email": "pwa-login@example.com", "password": "StrongPass123!"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:index"))
        self.assertTrue(self.client.session.get("gabsweb_pwa_prompt"))

        first_render = self.client.get(reverse("core:index"))
        self.assertContains(first_render, 'data-pwa-login-prompt="true"')
        self.assertNotIn("gabsweb_pwa_prompt", self.client.session)

        refresh = self.client.get(reverse("core:index"))
        self.assertContains(refresh, 'data-pwa-login-prompt="false"')

    def test_registration_prompts_once_after_sign_in(self):
        response = self.client.post(
            reverse("core:register"),
            {
                "full_name": "Pwa Person",
                "email": "pwa-register@example.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
                "workplace_access": "no",
                "terms": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:index"))
        self.assertTrue(self.client.session.get("gabsweb_pwa_prompt"))

        first_render = self.client.get(reverse("core:index"))
        self.assertContains(first_render, 'data-pwa-login-prompt="true"')
        self.assertNotIn("gabsweb_pwa_prompt", self.client.session)

    def test_each_new_login_session_prompts_again(self):
        from .models import User

        User.objects.create_user(
            email="pwa-one@example.com", password="StrongPass123!"
        )
        User.objects.create_user(
            email="pwa-two@example.com", password="StrongPass123!"
        )
        self.client.post(
            reverse("core:login"),
            {"email": "pwa-one@example.com", "password": "StrongPass123!"},
        )
        self.client.get(reverse("core:index"))
        self.client.post(reverse("core:logout"))

        self.client.post(
            reverse("core:login"),
            {"email": "pwa-two@example.com", "password": "StrongPass123!"},
        )
        next_login = self.client.get(reverse("core:index"))
        self.assertContains(next_login, 'data-pwa-login-prompt="true"')

    def test_no_custom_splash_screen_or_duplicate_auth_flow_was_added(self):
        pwa_source = read_static("assets/js/pwa.js")
        urls = (BASE_DIR / "Gabsweb_Cashflow" / "urls.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("splash", pwa_source.lower())
        self.assertIn("path('sw.js'", urls)
        self.assertIn("path('manifest.webmanifest'", urls)
        self.assertEqual(
            urls.count("path('manifest.webmanifest', core_pwa.pwa_manifest"), 1
        )
        self.assertEqual(
            urls.count("path('sw.js', core_pwa.pwa_service_worker"), 1
        )
