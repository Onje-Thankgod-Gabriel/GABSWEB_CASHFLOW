from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User

PASSWORD = "StrongPass123!"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class WorkplacePendingNoticeTests(TestCase):
    def make_user(self, email, status):
        user = User.objects.create_user(email=email, password=PASSWORD)
        user.account_status = status
        user.save(update_fields=["account_status"])
        return user

    def test_pending_user_sees_full_notice_once_after_login_then_compact(self):
        user = self.make_user("pending-notice@example.com", User.AccountStatus.PENDING)
        self.client.force_login(user)

        first = self.client.get(reverse("core:index")).content.decode("utf-8")
        self.assertIn("app-pending-notice is-expanded", first)
        self.assertIn('data-pending-notice', first)
        self.assertIn('data-pending-expanded="true"', first)
        self.assertIn(
            "Your request to access GCTN/Apex Pay workplace features is awaiting "
            "approval from the platform owner.",
            first,
        )

        second = self.client.get(reverse("core:index")).content.decode("utf-8")
        self.assertIn("app-pending-notice is-compact", second)
        self.assertNotIn("data-pending-expanded", second)
        self.assertIn("Workplace Access Pending", second)
        self.assertNotIn("is-expanded", second)

    def test_approved_and_general_users_never_see_pending_notice(self):
        for email, status in (
            ("approved-notice@example.com", User.AccountStatus.APPROVED),
            ("general-notice@example.com", User.AccountStatus.GENERAL),
        ):
            user = self.make_user(email, status)
            self.client.force_login(user)
            content = self.client.get(reverse("core:index")).content.decode("utf-8")
            self.assertNotIn("app-pending-notice", content)
            self.client.logout()

    def test_pending_status_is_not_changed_by_viewing_the_notice(self):
        user = self.make_user("still-pending@example.com", User.AccountStatus.PENDING)
        self.client.force_login(user)
        self.client.get(reverse("core:index"))
        self.client.get(reverse("core:index"))
        user.refresh_from_db()
        self.assertEqual(user.account_status, User.AccountStatus.PENDING)
