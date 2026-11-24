from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User

PORTFOLIO_URL = "https://gabsweb.com.ng/"

APP_ROUTES = (
    "core:index",
    "core:insights",
    "core:budgets",
    "core:goals",
    "core:planner",
    "core:savings",
    "core:settings",
    "core:transaction",
    "core:work_schedule",
)

AUTH_ROUTES = (
    "core:login",
    "core:register",
    "core:password_reset",
    "core:password_reset_done",
    "core:password_reset_complete",
)

ERROR_ROUTES = (
    "core:bad_request",
    "core:permission_denied",
    "core:page_not_found",
    "core:server_error",
)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class PortfolioLinkTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="portfolio@example.com", password="StrongPass123!"
        )
        self.client.force_login(self.user)

    def assert_single_portfolio_link(self, response):
        content = response.content.decode("utf-8")
        self.assertEqual(content.count('href="%s"' % PORTFOLIO_URL), 1)
        self.assertIn('target="_blank"', content)
        self.assertIn('rel="noopener noreferrer"', content)
        self.assertIn(">Gabsweb</a> experience", content)
        self.assertIn("Gabsweb CashFlow", content)

    def test_every_app_page_has_one_portfolio_link(self):
        for route in APP_ROUTES:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assert_single_portfolio_link(response)

    def test_auth_pages_have_one_portfolio_link(self):
        self.client.logout()
        for route in AUTH_ROUTES:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assert_single_portfolio_link(response)

    def test_error_pages_have_one_portfolio_link(self):
        for route in ERROR_ROUTES:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertIn(response.status_code, (200, 400, 403, 404, 500))
                self.assert_single_portfolio_link(response)

    def test_legal_pages_have_one_portfolio_link(self):
        for route in ("core:terms", "core:privacy_policy"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assert_single_portfolio_link(response)

    def test_offline_page_has_one_portfolio_link(self):
        offline = (Path(settings.BASE_DIR) / "static" / "assets" / "offline.html").read_text(
            encoding="utf-8"
        )

        self.assertEqual(offline.count('href="%s"' % PORTFOLIO_URL), 1)
        self.assertIn('target="_blank"', offline)
        self.assertIn('rel="noopener noreferrer"', offline)
        self.assertIn(">Gabsweb</a>", offline)
        self.assertIn("Gabsweb CashFlow", offline)

    def test_shared_layouts_use_the_portfolio_footer_partial(self):
        layouts = Path(settings.BASE_DIR) / "core" / "templates" / "layouts"

        for name in ("app_base.html", "auth_base.html", "error_base.html"):
            with self.subTest(layout=name):
                source = (layouts / name).read_text(encoding="utf-8")
                self.assertIn(
                    'include "partials/portfolio_footer.html"', source
                )
