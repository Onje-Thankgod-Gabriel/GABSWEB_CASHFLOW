"""Focused tests for Resend HTTPS API email delivery.

These tests never contact Resend. The Resend backend is exercised with a mocked
HTTP session, and the Anymail test backend is used where the rendered message
content matters. SMTP is patched in every delivery test so a regression that
silently falls back to SMTP fails the suite.
"""

from unittest.mock import Mock, patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from anymail.backends.resend import EmailBackend as ResendEmailBackend
from anymail.exceptions import AnymailAPIError, AnymailConfigurationError

from .models import Notification, User
from .notifications import (
    handle_new_registration,
    handle_user_login,
    send_notification_email,
)

PASSWORD = "StrongPass123!"

RESEND_MAILERS = {
    "default": {
        "BACKEND": "anymail.backends.resend.EmailBackend",
        "OPTIONS": {},
    }
}

TEST_MAILERS = {
    "default": {
        "BACKEND": "anymail.backends.test.EmailBackend",
        "OPTIONS": {},
    }
}

RESEND_SETTINGS = {
    "MAILERS": RESEND_MAILERS,
    "ANYMAIL": {"RESEND_API_KEY": "test-key-not-real", "REQUESTS_TIMEOUT": 5},
    "DEFAULT_FROM_EMAIL": "Gabsweb CashFlow <no-reply@example.com>",
    "PASSWORD_RESET_BASE_URL": "https://cashflow.example.com",
    "EMAIL_LOGO_URL": (
        "https://cashflow.example.com/static/assets/images/"
        "gabsweb-cashflow-logo.png"
    ),
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
}


def resend_response(payload=None, status=200):
    response = Mock()
    response.status_code = status
    response.reason = "OK" if status < 300 else "Error"
    response.headers = {"Content-Type": "application/json"}
    response.text = "{}"
    response.json.return_value = (
        payload if payload is not None else {"id": "email_test_123"}
    )
    return response


class ResendBackendConfigurationTests(TestCase):
    def test_default_mailer_builds_the_resend_https_backend(self):
        with override_settings(**RESEND_SETTINGS):
            connection = mail.mailers.create_connection("default")

        self.assertIsInstance(connection, ResendEmailBackend)
        self.assertEqual(connection.api_url, "https://api.resend.com/")
        self.assertTrue(connection.api_url.startswith("https://"))
        self.assertNotIn("smtp", connection.api_url.lower())

    def test_missing_api_key_fails_clearly_instead_of_using_smtp(self):
        with override_settings(MAILERS=RESEND_MAILERS, ANYMAIL={}):
            with patch("smtplib.SMTP") as smtp:
                with self.assertRaises(AnymailConfigurationError):
                    mail.send_mail(
                        subject="Subject",
                        message="Body",
                        from_email=None,
                        recipient_list=["user@example.com"],
                    )
        smtp.assert_not_called()


class ResendDeliveryTests(TestCase):
    def test_standard_send_mail_posts_to_resend_over_https(self):
        with override_settings(**RESEND_SETTINGS):
            with patch(
                "anymail.backends.base_requests.requests.Session.request",
                return_value=resend_response(),
            ) as request:
                with patch("smtplib.SMTP") as smtp, patch(
                    "smtplib.SMTP_SSL"
                ) as smtp_ssl:
                    sent = mail.send_mail(
                        subject="Hello",
                        message="Plain body",
                        from_email=None,
                        recipient_list=["user@example.com"],
                    )

        self.assertEqual(sent, 1)
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()
        self.assertTrue(request.called)
        url = request.call_args.kwargs["url"]
        self.assertTrue(url.startswith("https://api.resend.com/"))
        self.assertEqual(request.call_args.kwargs["method"], "POST")

    def test_password_reset_email_uses_resend_https_api(self):
        user = User.objects.create_user(
            email="reset@example.com", password=PASSWORD
        )

        with override_settings(**RESEND_SETTINGS):
            with patch(
                "anymail.backends.base_requests.requests.Session.request",
                return_value=resend_response(),
            ) as request:
                with patch("smtplib.SMTP") as smtp, patch(
                    "smtplib.SMTP_SSL"
                ) as smtp_ssl:
                    response = self.client.post(
                        reverse("core:password_reset"), {"email": user.email}
                    )

        self.assertRedirects(response, reverse("core:password_reset_done"))
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()
        urls = [call.kwargs.get("url", "") for call in request.call_args_list]
        self.assertTrue(
            any(url.startswith("https://api.resend.com/") for url in urls),
            f"Expected a Resend HTTPS request, got {urls!r}",
        )

    def test_password_reset_preserves_html_and_plain_text_parts(self):
        user = User.objects.create_user(
            email="reset@example.com", password=PASSWORD
        )

        with override_settings(
            MAILERS=TEST_MAILERS,
            DEFAULT_FROM_EMAIL="Gabsweb CashFlow <no-reply@example.com>",
            PASSWORD_RESET_BASE_URL="https://cashflow.example.com",
            EMAIL_LOGO_URL=(
                "https://cashflow.example.com/static/assets/images/"
                "gabsweb-cashflow-logo.png"
            ),
            PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
        ):
            response = self.client.post(
                reverse("core:password_reset"), {"email": user.email}
            )

        self.assertRedirects(response, reverse("core:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("Reset Your Password", message.body)
        self.assertIn("/password-reset-confirm/", message.body)
        self.assertNotIn("<html", message.body)
        self.assertTrue(
            any(
                content_type == "text/html"
                for _, content_type in message.alternatives
            )
        )
        html_body = message.alternatives[0][0]
        self.assertIn("/password-reset-confirm/", html_body)
        self.assertIn('src="https://cashflow.example.com/', html_body)

    def test_welcome_and_login_emails_use_the_configured_backend(self):
        user = User.objects.create_user(
            email="member@example.com", password=PASSWORD, first_name="Mary"
        )

        with override_settings(
            MAILERS=TEST_MAILERS,
            DEFAULT_FROM_EMAIL="Gabsweb CashFlow <no-reply@example.com>",
            PASSWORD_RESET_BASE_URL="https://cashflow.example.com",
        ):
            with self.captureOnCommitCallbacks(execute=True):
                handle_new_registration(user)
            self.assertEqual(len(mail.outbox), 1)
            self.assertIn("welcome", mail.outbox[0].subject.lower())
            self.assertEqual(mail.outbox[0].to, [user.email])

            mail.outbox.clear()
            with self.captureOnCommitCallbacks(execute=True):
                handle_user_login(user)
            self.assertEqual(len(mail.outbox), 1)
            self.assertIn("sign-in", mail.outbox[0].subject.lower())
            self.assertEqual(mail.outbox[0].to, [user.email])

    def test_api_error_is_raised_without_attempting_smtp(self):
        with override_settings(**RESEND_SETTINGS):
            with patch(
                "anymail.backends.base_requests.requests.Session.request",
                return_value=resend_response(status=500),
            ):
                with patch("smtplib.SMTP") as smtp:
                    with self.assertRaises(AnymailAPIError):
                        mail.send_mail(
                            subject="Subject",
                            message="Body",
                            from_email=None,
                            recipient_list=["user@example.com"],
                        )
        smtp.assert_not_called()

    def test_notification_records_api_error_and_keeps_in_app_record(self):
        user = User.objects.create_user(
            email="member@example.com", password=PASSWORD, first_name="Mary"
        )
        notification = Notification.objects.create(
            owner=user,
            title="Welcome",
            message="Hello",
            event_type=Notification.EventType.WELCOME,
            email_status=Notification.EmailStatus.PENDING,
        )

        with override_settings(**RESEND_SETTINGS):
            with patch(
                "anymail.backends.base_requests.requests.Session.request",
                return_value=resend_response(status=500),
            ):
                with patch("smtplib.SMTP") as smtp:
                    delivered = send_notification_email(notification.pk)

        self.assertFalse(delivered)
        smtp.assert_not_called()
        notification.refresh_from_db()
        self.assertEqual(
            notification.email_status, Notification.EmailStatus.FAILED
        )
        self.assertEqual(notification.email_attempts, 1)
        self.assertTrue(notification.email_last_error)
        self.assertEqual(notification.title, "Welcome")
