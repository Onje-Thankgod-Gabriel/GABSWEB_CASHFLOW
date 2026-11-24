"""Focused tests for Google linking, password setup and recovery safeguards."""

from pathlib import Path

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from allauth.socialaccount.models import SocialAccount

from .models import User
from .notifications import handle_new_registration

PASSWORD = "V7x!pQ2mR9#L"
NEW_PASSWORD = "NewV7x!pQ2mR9#L"

GOOGLE_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": "test-client-id",
            "secret": "test-client-secret",
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "OAUTH_PKCE_ENABLED": True,
        "FETCH_USERINFO": True,
    }
}

AUTH_TEST_SETTINGS = {
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
    "MAILERS": {
        "default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}
    },
    "DEFAULT_FROM_EMAIL": "Gabsweb CashFlow <no-reply@example.com>",
    "PASSWORD_RESET_BASE_URL": "https://cashflow.example.com",
    "EMAIL_LOGO_URL": (
        "https://cashflow.example.com/static/assets/images/"
        "gabsweb-cashflow-logo.png"
    ),
    "GOOGLE_OAUTH_CONFIGURED": True,
    "SOCIALACCOUNT_PROVIDERS": GOOGLE_PROVIDERS,
    "DOCUMENTATION_URL_NAME": "core:documentation",
}


def make_google_account(user, uid="google-uid"):
    return SocialAccount.objects.create(
        user=user,
        provider="google",
        uid=uid,
        extra_data={
            "email": user.email,
            "email_verified": True,
            "given_name": user.first_name,
            "family_name": user.last_name,
        },
    )


@override_settings(**AUTH_TEST_SETTINGS)
class GoogleOnlyPasswordRecoveryTests(TestCase):
    def setUp(self):
        cache.clear()

    def make_google_only_user(self, email="google-only@example.com", **extra):
        user = User.objects.create_user(email=email, password=None, **extra)
        make_google_account(user, uid=f"google-{email}")
        return user

    def request_reset(self, email):
        return self.client.post(
            reverse("core:password_reset"), {"email": email}
        )

    def test_google_only_user_receives_google_guidance_not_a_reset_link(self):
        user = self.make_google_only_user(first_name="Ada")

        response = self.request_reset(user.email)

        self.assertRedirects(response, reverse("core:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [user.email])
        self.assertIn("Sign in to Gabsweb CashFlow with Google", message.subject)
        self.assertIn("does not have a Gabsweb password", message.body)
        self.assertIn("google", message.body.lower())
        self.assertNotIn("/password-reset-confirm/", message.body)
        self.assertNotIn("password-reset-confirm", message.alternatives[0][0])

        user.refresh_from_db()
        self.assertFalse(user.has_usable_password())

    def test_google_only_recovery_email_is_branded_and_targets_google_login(self):
        user = self.make_google_only_user()

        self.request_reset(user.email)

        html_body = mail.outbox[0].alternatives[0][0]
        self.assertIn("Gabsweb CashFlow", html_body)
        self.assertIn(settings.EMAIL_LOGO_URL, html_body)
        self.assertIn("https://cashflow.example.com/accounts/google/login/", html_body)
        self.assertIn("mso-padding-alt:12px 20px", html_body)

    def test_normal_password_account_still_receives_the_reset_link(self):
        user = User.objects.create_user(
            email="password@example.com", password=PASSWORD
        )

        self.request_reset(user.email)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("/password-reset-confirm/", message.body)
        self.assertNotIn("created with Google", message.body)
        self.assertNotIn("does not have a Gabsweb password", message.body)

    def test_google_connected_account_with_local_password_receives_reset_link(self):
        user = User.objects.create_user(
            email="both@example.com", password=PASSWORD
        )
        make_google_account(user)

        self.request_reset(user.email)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("/password-reset-confirm/", message.body)
        self.assertNotIn("does not have a Gabsweb password", message.body)

    def test_inactive_google_only_account_receives_no_email(self):
        user = self.make_google_only_user(is_active=False)

        response = self.request_reset(user.email)

        self.assertRedirects(response, reverse("core:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_unregistered_address_gets_identical_public_response_and_no_email(self):
        user = self.make_google_only_user()
        registered = self.request_reset(user.email)
        mail.outbox.clear()
        missing = self.request_reset("missing@example.com")

        self.assertEqual(registered.status_code, missing.status_code)
        self.assertEqual(registered["Location"], missing["Location"])
        self.assertEqual(len(mail.outbox), 0)


@override_settings(**AUTH_TEST_SETTINGS)
class PasswordSetupAndChangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="google-only@example.com", password=None
        )
        self.google = make_google_account(self.user)
        self.client.force_login(self.user)

    def test_google_only_user_can_set_a_password_without_losing_google(self):
        response = self.client.post(
            reverse("core:set_password"),
            {
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )

        self.assertRedirects(response, reverse("core:settings"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_usable_password())
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertTrue(
            SocialAccount.objects.filter(
                user=self.user, provider="google"
            ).exists()
        )
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

        self.client.logout()
        login = self.client.post(
            reverse("core:login"),
            {"email": self.user.email, "password": NEW_PASSWORD},
        )
        self.assertRedirects(login, reverse("core:index"))

    def test_setting_a_password_refuses_to_overwrite_an_existing_password(self):
        self.user.set_password(PASSWORD)
        self.user.save(update_fields=["password"])
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("core:set_password"),
            {
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )

        self.assertRedirects(response, reverse("core:settings"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))
        self.assertFalse(self.user.check_password(NEW_PASSWORD))

    def test_setting_a_password_requires_a_linked_google_identity(self):
        self.google.delete()
        response = self.client.post(
            reverse("core:set_password"),
            {
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )

        self.assertRedirects(response, reverse("core:settings"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())

    def test_setting_a_password_rejects_weak_passwords(self):
        response = self.client.post(
            reverse("core:set_password"),
            {"new_password1": "12345678901", "new_password2": "12345678901"},
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())

    def test_setting_a_password_requires_authentication(self):
        client = Client()
        response = client.post(
            reverse("core:set_password"),
            {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
        )

        self.assertRedirects(
            response,
            f"{reverse('core:login')}?next={reverse('core:set_password')}",
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())

    def test_local_password_can_be_changed_and_google_stays_connected(self):
        self.user.set_password(PASSWORD)
        self.user.save(update_fields=["password"])
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("core:change_password"),
            {
                "old_password": PASSWORD,
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )

        self.assertRedirects(response, reverse("core:settings"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertFalse(self.user.check_password(PASSWORD))
        self.assertTrue(
            SocialAccount.objects.filter(
                user=self.user, provider="google"
            ).exists()
        )
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_change_password_rejects_a_wrong_current_password(self):
        self.user.set_password(PASSWORD)
        self.user.save(update_fields=["password"])
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("core:change_password"),
            {
                "old_password": "wrong-password",
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))
        self.assertFalse(self.user.check_password(NEW_PASSWORD))

    def test_google_only_account_cannot_use_change_password(self):
        response = self.client.post(
            reverse("core:change_password"),
            {
                "old_password": PASSWORD,
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )

        self.assertRedirects(response, reverse("core:settings"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())


@override_settings(**AUTH_TEST_SETTINGS)
class GoogleConnectionSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="member@example.com", password=PASSWORD
        )

    def test_unlinked_password_account_sees_not_connected_and_connect_action(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:settings"))

        self.assertContains(response, "Not connected")
        self.assertContains(response, "Connect Google")
        self.assertContains(response, "Update password")
        self.assertNotContains(response, "Disconnect Google")

    def test_linked_account_sees_connected_status_and_disconnect_action(self):
        make_google_account(self.user)
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:settings"))

        self.assertContains(response, "Connected")
        self.assertContains(response, "Disconnect Google")
        self.assertContains(response, self.user.email)

    def test_google_only_account_sees_set_password_and_cannot_disconnect(self):
        user = User.objects.create_user(
            email="google-only@example.com", password=None
        )
        make_google_account(user)
        self.client.force_login(user)
        response = self.client.get(reverse("core:settings"))

        self.assertContains(response, "Connected")
        self.assertContains(response, "Set password")
        self.assertContains(
            response,
            "Set a Gabsweb password below before disconnecting Google.",
        )
        self.assertNotContains(response, "Disconnect Google")

    def test_connected_google_account_can_be_disconnected_with_a_password(self):
        make_google_account(self.user)
        self.client.force_login(self.user)

        response = self.client.post(reverse("core:google_disconnect"))

        self.assertRedirects(response, reverse("core:settings"))
        self.assertFalse(
            SocialAccount.objects.filter(
                user=self.user, provider="google"
            ).exists()
        )
        self.assertTrue(self.user.has_usable_password())
        self.client.logout()
        login = self.client.post(
            reverse("core:login"),
            {"email": self.user.email, "password": PASSWORD},
        )
        self.assertRedirects(login, reverse("core:index"))

    def test_google_only_account_cannot_be_locked_out_by_disconnect(self):
        user = User.objects.create_user(
            email="google-only@example.com", password=None
        )
        account = make_google_account(user)
        self.client.force_login(user)

        response = self.client.post(reverse("core:google_disconnect"))

        self.assertRedirects(response, reverse("core:settings"))
        self.assertTrue(SocialAccount.objects.filter(pk=account.pk).exists())

    def test_disconnect_requires_post_csrf_and_authentication(self):
        make_google_account(self.user)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)

        self.assertEqual(
            client.get(reverse("core:google_disconnect")).status_code, 405
        )
        self.assertEqual(
            client.post(reverse("core:google_disconnect")).status_code, 403
        )

        anonymous = Client()
        response = anonymous.post(reverse("core:google_disconnect"))
        self.assertRedirects(
            response,
            f"{reverse('core:login')}?next={reverse('core:google_disconnect')}",
        )


@override_settings(**AUTH_TEST_SETTINGS)
class WelcomeEmailDocumentationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="new-member@example.com",
            password=PASSWORD,
            first_name="Mary",
        )

    def send_welcome(self):
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            handle_new_registration(self.user)
        self.assertEqual(len(mail.outbox), 1)
        return mail.outbox[0]

    def test_welcome_email_includes_the_documentation_link(self):
        message = self.send_welcome()

        self.assertIn("Read how it works", message.body)
        self.assertIn("/documentation/", message.body)
        html_body = message.alternatives[0][0]
        self.assertIn("Read how it works", html_body)
        self.assertIn("/documentation/", html_body)

    def test_welcome_email_renders_a_named_docs_route_when_available(self):
        with self.settings(DOCUMENTATION_URL_NAME="core:privacy_policy"):
            message = self.send_welcome()

        expected = "https://cashflow.example.com/privacy-policy/"
        self.assertIn("Read how it works and how to get started", message.body)
        self.assertIn(expected, message.body)
        html_body = message.alternatives[0][0]
        self.assertIn("Read how it works and how to get started", html_body)
        self.assertIn(expected, html_body)

    def test_welcome_email_preserves_branding_logo_and_button(self):
        message = self.send_welcome()
        html_body = message.alternatives[0][0]

        self.assertIn("Welcome to Gabsweb CashFlow", message.subject)
        self.assertIn("Welcome to Gabsweb CashFlow", html_body)
        self.assertIn(settings.EMAIL_LOGO_URL, html_body)
        self.assertIn("Open Gabsweb CashFlow", html_body)
        self.assertIn("padding:12px 20px", html_body)


@override_settings(**AUTH_TEST_SETTINGS)
class TransactionalEmailButtonTests(TestCase):
    def email_templates(self):
        root = Path(settings.BASE_DIR) / "core/templates/templates/emails"
        return sorted(root.glob("*_email.html"))

    def test_every_html_email_uses_compact_consistent_buttons(self):
        templates = self.email_templates()
        self.assertGreaterEqual(len(templates), 5)
        for template in templates:
            with self.subTest(template=template.name):
                source = template.read_text(encoding="utf-8")
                self.assertIn('name="viewport"', source)
                self.assertIn('role="presentation"', source)
                self.assertIn("max-width:600px", source)
                self.assertIn("padding:12px 20px", source)
                self.assertIn("font-size:14px", source)
                self.assertIn("mso-padding-alt:12px 20px", source)
                self.assertIn("border-radius:10px", source)
                self.assertIn("white-space:nowrap", source)
                self.assertIn("word-break:break-all", source)
                self.assertIn("class=\"email-content\"", source)
                self.assertIn("class=\"email-button\"", source)
                self.assertIn("@media only screen and (max-width: 480px)", source)
                self.assertNotIn("padding:14px 24px", source)
                self.assertNotIn(
                    "font-size:16px;line-height:20px;font-weight:700;"
                    "text-decoration:none;",
                    source,
                )

    def test_email_buttons_have_short_labels_that_fit_narrow_clients(self):
        for template in self.email_templates():
            with self.subTest(template=template.name):
                source = template.read_text(encoding="utf-8")
                labels = []
                marker = "white-space:nowrap;\">"
                for chunk in source.split(marker)[1:]:
                    labels.append(chunk.split("</a>", 1)[0])
                self.assertTrue(labels, "No email button label found.")
                for label in labels:
                    self.assertLessEqual(len(label), 26, label)
