import re
from pathlib import Path

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User
from .notifications import handle_new_registration

PASSWORD = "StrongPass123!"

EMAIL_SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "DEFAULT_FROM_EMAIL": "Gabsweb CashFlow <no-reply@example.com>",
    "PASSWORD_RESET_BASE_URL": "https://cashflow.example.com",
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
}

ROOT = Path(__file__).resolve().parent.parent


def read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DocumentationPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="reader@example.com", password=PASSWORD
        )
        self.url = reverse("core:documentation")

    def test_route_resolves_to_core_documentation(self):
        self.assertEqual(self.url, "/documentation/")

    def test_authenticated_user_can_access_documentation(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gabsweb CashFlow Documentation")

    def test_unauthenticated_user_is_redirected_through_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:login"), response.url)
        self.assertIn("next=", response.url)
        self.assertIn(self.url, response.url)

    def test_login_returns_user_to_documentation(self):
        response = self.client.post(
            reverse("core:login"),
            {
                "email": self.user.email,
                "password": PASSWORD,
                "next": self.url,
            },
        )
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_page_uses_the_authenticated_layout(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "documentation.html")
        self.assertTemplateUsed(response, "layouts/app_base.html")
        self.assertTemplateUsed(response, "layouts/base.html")

    def test_sidebar_contains_documentation_immediately_before_settings(self):
        self.client.force_login(self.user)
        html = self.client.get(self.url).content.decode("utf-8")
        sidebar_start = html.index('<aside id="sidebar"')
        sidebar = html[sidebar_start : html.index("</aside>", sidebar_start)]

        self.assertIn('href="%s"' % self.url, sidebar)
        self.assertIn("<span>Documentation</span>", sidebar)
        self.assertLess(
            sidebar.index("<span>Documentation</span>"),
            sidebar.index("<span>Settings</span>"),
        )
        self.assertRegex(
            sidebar,
            re.escape("<span>Documentation</span>")
            + r"\s*</a>\s*<a href=\""
            + re.escape(reverse("core:settings"))
            + r"\" class=\"nav-item\"",
        )

    def test_documentation_is_not_in_the_mobile_bottom_navigation(self):
        self.client.force_login(self.user)
        html = self.client.get(self.url).content.decode("utf-8")
        start = html.index('<nav class="bottom-nav')
        bottom_nav = html[start : html.index("</nav>", start)]

        self.assertNotIn(self.url, bottom_nav)
        self.assertNotIn("Documentation", bottom_nav)

        script = read("static/assets/js/global-navigation.js")
        self.assertNotIn('href: "/documentation/"', script)
        self.assertNotIn('label: "Documentation"', script)

    def test_all_approved_sections_are_present(self):
        self.client.force_login(self.user)
        html = self.client.get(self.url).content.decode("utf-8")
        section_ids = (
            "welcome",
            "dashboard",
            "my-finances",
            "budget",
            "savings",
            "goals",
            "planner",
            "work-schedule",
            "reports",
            "settings",
            "connections",
            "need-help",
        )
        for section_id in section_ids:
            with self.subTest(section=section_id):
                self.assertIn('id="%s"' % section_id, html)

        headings = (
            "Welcome to Gabsweb CashFlow",
            "1. Dashboard",
            "2. My Finances",
            "3. Budget &amp; Money Allocation",
            "4. Savings",
            "5. Goals",
            "6. Planner",
            "7. Work Schedule",
            "8. Reports &amp; Insights",
            "9. Settings",
            "10. Understanding how your finances connect",
            "Need help?",
        )
        for heading in headings:
            with self.subTest(heading=heading):
                self.assertIn(heading, html)

    def test_important_financial_rules_are_present(self):
        self.client.force_login(self.user)
        html = self.client.get(self.url).content.decode("utf-8")
        rules = (
            "Available balance is calculated by subtracting your current Savings balance",
            "it is not a measure of Budget Savings allocations",
            "An expense linked to a Budget allocation must fall within that allocation's",
            "This figure never displays as a negative",
            "Over-allocation and overspending are different",
            "Recording a withdrawal is a Savings record only",
            "A fully funded Goal does not automatically mean that you have purchased the item",
            "contact the platform owner for approval",
            "Only recorded transactions change account balances",
            "A Goal purchase must be recorded as an expense in My Finances",
        )
        for rule in rules:
            with self.subTest(rule=rule):
                self.assertIn(rule, html)

    def test_page_uses_separate_documentation_assets_and_theme_tokens(self):
        self.client.force_login(self.user)
        html = self.client.get(self.url).content.decode("utf-8")
        self.assertIn("assets/css/documentation.css", html)
        self.assertIn("assets/js/documentation.js", html)

        css = read("static/assets/css/documentation.css")
        self.assertIn("position: sticky", css)
        self.assertIn("@media (max-width:", css)
        self.assertIn(".docs-toc-link.is-active", css)
        self.assertIn('content: "\\2013"', css)
        self.assertNotIn("border-left:", css)
        self.assertIn("var(--text)", css)
        self.assertIn("var(--border)", css)

    def test_navigation_script_handles_missing_sections_safely(self):
        script = read("static/assets/js/documentation.js")
        self.assertIn("if (!links.length) return;", script)
        self.assertIn("if (!sections.length) return;", script)
        self.assertIn("getElementById(id)", script)


@override_settings(**EMAIL_SETTINGS)
class DocumentationWelcomeEmailTests(TestCase):
    def test_welcome_email_contains_absolute_documentation_link(self):
        user = User.objects.create_user(
            email="newmember@example.com", password=PASSWORD, first_name="Ada"
        )
        with self.captureOnCommitCallbacks(execute=True):
            handle_new_registration(user)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        expected = "https://cashflow.example.com/documentation/"
        self.assertIn(expected, message.body)
        self.assertTrue(
            any(
                content_type == "text/html"
                for _, content_type in message.alternatives
            )
        )
        html_body = message.alternatives[0][0]
        self.assertIn('href="%s"' % expected, html_body)
        self.assertIn("Read how it works", html_body)

    def test_welcome_email_omits_link_when_configuration_is_absent(self):
        user = User.objects.create_user(
            email="absent@example.com", password=PASSWORD, first_name="Ada"
        )
        with override_settings(DOCUMENTATION_URL_NAME=""):
            with self.captureOnCommitCallbacks(execute=True):
                handle_new_registration(user)

        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn("/documentation/", mail.outbox[0].body)

    def test_welcome_email_does_not_fail_when_route_is_unavailable(self):
        user = User.objects.create_user(
            email="unavailable@example.com", password=PASSWORD, first_name="Ada"
        )
        with override_settings(DOCUMENTATION_URL_NAME="core:missing-docs-route"):
            with self.captureOnCommitCallbacks(execute=True):
                handle_new_registration(user)

        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn("/documentation/", mail.outbox[0].body)
        self.assertIn("Welcome to Gabsweb CashFlow", mail.outbox[0].body)
