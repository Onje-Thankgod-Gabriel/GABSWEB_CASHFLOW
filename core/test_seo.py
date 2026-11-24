import json
import re

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User

PASSWORD = "StrongPass123!"

PRODUCTION_BASE = "https://cashflow.gabsweb.com.ng"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PublicMetadataTests(TestCase):
    def _meta(self, content, name):
        match = re.search(
            rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]*)"',
            content,
        )
        return match.group(1) if match else None

    def _property(self, content, prop):
        match = re.search(
            rf'<meta\s+property="{re.escape(prop)}"\s+content="([^"]*)"',
            content,
        )
        return match.group(1) if match else None

    def _canonical(self, content):
        return re.findall(r'<link\s+rel="canonical"\s+href="([^"]*)"', content)

    def _json_ld(self, content):
        match = re.search(
            r'<script type="application/ld\+json">(.*?)</script>',
            content,
            re.S,
        )
        self.assertIsNotNone(match, "Expected a JSON-LD script block")
        return json.loads(match.group(1))

    def assert_public_page(self, url, title, description, path):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn(f"<title>{title}</title>", content)
        self.assertEqual(self._meta(content, "description"), description)
        self.assertEqual(self._canonical(content), [f"{PRODUCTION_BASE}{path}"])
        self.assertEqual(
            self._meta(content, "robots"),
            "index, follow, max-image-preview:large",
        )

        self.assertEqual(self._property(content, "og:title"), title)
        self.assertEqual(self._property(content, "og:url"), f"{PRODUCTION_BASE}{path}")
        self.assertEqual(self._property(content, "og:type"), "website")
        self.assertEqual(self._property(content, "og:site_name"), "Gabsweb CashFlow")
        self.assertEqual(
            self._property(content, "og:image"),
            f"{PRODUCTION_BASE}/static/assets/images/gabsweb-cashflow-logo.png",
        )

        self.assertEqual(self._meta(content, "twitter:card"), "summary")
        self.assertEqual(self._meta(content, "twitter:title"), title)

        data = self._json_ld(content)
        types = [node.get("@type") for node in data.get("@graph", [])]
        self.assertIn("Organization", types)
        self.assertIn("WebSite", types)
        self.assertIn("WebPage", types)

        return content

    def test_privacy_policy_has_unique_public_metadata(self):
        self.assert_public_page(
            reverse("core:privacy_policy"),
            "Privacy Policy | Gabsweb CashFlow",
            "Learn how Gabsweb CashFlow handles your information, account "
            "details and financial data in this privacy policy.",
            "/privacy-policy/",
        )

    def test_terms_has_unique_public_metadata(self):
        self.assert_public_page(
            reverse("core:terms"),
            "Terms and Conditions | Gabsweb CashFlow",
            "Understand the terms that govern access to and use of the "
            "Gabsweb CashFlow personal finance application.",
            "/terms/",
        )

    def test_public_pages_do_not_leak_private_data(self):
        for url in (reverse("core:privacy_policy"), reverse("core:terms")):
            content = self.client.get(url).content.decode("utf-8")
            self.assertNotIn("/login/", content)
            self.assertNotIn("/documentation/", content)
            self.assertNotIn("data-user-email", content)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PrivateMetadataTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="seo-private@example.com", password=PASSWORD
        )

    def assert_noindex(self, response):
        content = response.content.decode("utf-8")
        self.assertIn('name="robots" content="noindex, nofollow, noarchive"', content)
        self.assertNotIn('rel="canonical"', content)
        self.assertNotIn('property="og:title"', content)

    def test_login_page_is_not_indexable(self):
        self.assert_noindex(self.client.get(reverse("core:login")))

    def test_register_page_is_not_indexable(self):
        self.assert_noindex(self.client.get(reverse("core:register")))

    def test_app_dashboard_is_not_indexable(self):
        self.client.force_login(self.user)
        self.assert_noindex(self.client.get(reverse("core:index")))

    def test_documentation_requires_login_and_is_not_indexable(self):
        response = self.client.get(reverse("core:documentation"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("core:login")))

        self.client.force_login(self.user)
        self.assert_noindex(self.client.get(reverse("core:documentation")))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class RobotsAndSitemapTests(TestCase):
    def test_robots_txt_defaults_to_deny_with_public_allowances(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        content = response.content.decode("utf-8")

        self.assertIn("User-agent: *", content)
        self.assertIn("Allow: /privacy-policy/", content)
        self.assertIn("Allow: /terms/", content)
        self.assertIn("Disallow: /", content)
        self.assertIn(f"Sitemap: {PRODUCTION_BASE}/sitemap.xml", content)

    def test_sitemap_lists_only_public_absolute_https_urls(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        content = response.content.decode("utf-8")

        urls = re.findall(r"<loc>(.*?)</loc>", content)
        self.assertEqual(
            sorted(urls),
            sorted(
                [
                    f"{PRODUCTION_BASE}/privacy-policy/",
                    f"{PRODUCTION_BASE}/terms/",
                ]
            ),
        )
        for url in urls:
            self.assertTrue(url.startswith("https://"))

        for private_path in (
            "/login/",
            "/register/",
            "/documentation/",
            "/insights/",
            "/settings/",
            "/work-schedule/",
            "/accounts/",
        ):
            self.assertNotIn(private_path, content)

    @override_settings(SITE_BASE_URL="https://staging.example.test/")
    def test_site_base_url_override_drives_canonical_and_sitemap(self):
        privacy = self.client.get(reverse("core:privacy_policy"))
        self.assertIn(
            'href="https://staging.example.test/privacy-policy/"',
            privacy.content.decode("utf-8"),
        )
        sitemap = self.client.get("/sitemap.xml").content.decode("utf-8")
        self.assertIn("https://staging.example.test/privacy-policy/", sitemap)
        self.assertIn(
            "Sitemap: https://staging.example.test/sitemap.xml",
            self.client.get("/robots.txt").content.decode("utf-8"),
        )
