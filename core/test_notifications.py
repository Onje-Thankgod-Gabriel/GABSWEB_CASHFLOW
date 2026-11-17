import json
from datetime import date
from decimal import Decimal

from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    BudgetAllocation,
    BudgetCategory,
    MonthlyBudget,
    Notification,
    User,
)

PASSWORD = "StrongPass123!"


class FailingEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise RuntimeError("Email provider unavailable")


LOCMEM_EMAIL = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "DEFAULT_FROM_EMAIL": "Gabsweb CashFlow <notifications@example.com>",
    "PASSWORD_RESET_BASE_URL": "https://cashflow.example.com",
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
}


@override_settings(**LOCMEM_EMAIL)
class ProfessionalRoleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="role@example.com", password=PASSWORD
        )
        self.other = User.objects.create_user(
            email="other@example.com", password=PASSWORD
        )
        self.client.force_login(self.user)

    def update(self, role, **overrides):
        payload = {
            "name": "Role Person",
            "email": self.user.email,
            "professional_role": role,
        }
        payload.update(overrides)
        return self.client.post(reverse("core:update_profile"), payload)

    def test_user_can_save_and_update_professional_role(self):
        response = self.update("  Digital   Sales Administrator  ")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["professional_role"], "Digital Sales Administrator")
        self.user.refresh_from_db()
        self.assertEqual(self.user.professional_role, "Digital Sales Administrator")

        response = self.update("Accountant")
        self.assertEqual(response.status_code, 200, response.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.professional_role, "Accountant")

    def test_role_persists_after_fresh_authenticated_request(self):
        self.update("Software Developer")
        self.client.logout()
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Software Developer")

    def test_whitespace_only_role_is_stored_as_empty(self):
        response = self.update("   ")
        self.assertEqual(response.status_code, 200, response.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.professional_role, "")

    def test_role_has_a_sensible_length_limit(self):
        response = self.update("x" * 81)
        self.assertEqual(response.status_code, 400)
        self.assertIn("professional_role", response.json()["errors"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.professional_role, "")

    def test_role_update_does_not_grant_permissions(self):
        self.update("Administrator")
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertEqual(self.user.account_status, User.AccountStatus.GENERAL)

    def test_user_cannot_modify_another_users_role(self):
        self.other.professional_role = "Teacher"
        self.other.save(update_fields=["professional_role"])
        self.update("Accountant")
        self.other.refresh_from_db()
        self.assertEqual(self.other.professional_role, "Teacher")


@override_settings(**LOCMEM_EMAIL)
class NotificationApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="notified@example.com", password=PASSWORD
        )
        self.other = User.objects.create_user(
            email="other@example.com", password=PASSWORD
        )
        self.client.force_login(self.user)
        Notification.objects.filter(owner=self.user).delete()

    def create_notification(self, owner=None, **overrides):
        values = {
            "owner": owner or self.user,
            "title": "Application Approved",
            "message": "Your application was approved.",
            "event_type": Notification.EventType.MEMBERSHIP_APPROVED,
        }
        values.update(overrides)
        return Notification.objects.create(**values)

    def test_empty_state_returns_no_notifications(self):
        response = self.client.get(reverse("core:notifications_api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["notifications"], [])
        self.assertEqual(payload["unreadCount"], 0)

    def test_only_the_authenticated_users_notifications_are_returned(self):
        mine = self.create_notification()
        self.create_notification(owner=self.other, title="Other user")
        response = self.client.get(reverse("core:notifications_api"))
        payload = response.json()
        self.assertEqual(len(payload["notifications"]), 1)
        self.assertEqual(payload["notifications"][0]["id"], str(mine.pk))
        self.assertEqual(payload["unreadCount"], 1)

    def test_mark_read_updates_state_and_unread_count(self):
        notification = self.create_notification()
        response = self.client.post(
            reverse("core:notification_read", args=[notification.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unreadCount"], 0)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertIsNotNone(notification.read_at)

    def test_users_cannot_read_another_users_notification(self):
        other = self.create_notification(owner=self.other)
        response = self.client.post(
            reverse("core:notification_read", args=[other.pk])
        )
        self.assertEqual(response.status_code, 404)
        other.refresh_from_db()
        self.assertFalse(other.is_read)

    def test_mark_all_read_only_affects_own_notifications(self):
        self.create_notification()
        self.create_notification(title="Second")
        other = self.create_notification(owner=self.other)
        response = self.client.post(reverse("core:notifications_read_all"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unreadCount"], 0)
        self.assertEqual(
            Notification.objects.filter(owner=self.user, is_read=False).count(), 0
        )
        other.refresh_from_db()
        self.assertFalse(other.is_read)

    def test_read_endpoints_require_post(self):
        notification = self.create_notification()
        response = self.client.get(
            reverse("core:notification_read", args=[notification.pk])
        )
        self.assertEqual(response.status_code, 405)

    def test_state_changing_notification_endpoints_require_csrf(self):
        from django.test import Client

        notification = self.create_notification()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(
            reverse("core:notification_read", args=[notification.pk])
        )
        self.assertEqual(response.status_code, 403)
        notification.refresh_from_db()
        self.assertFalse(notification.is_read)


@override_settings(**LOCMEM_EMAIL)
class ApprovalNotificationTests(TestCase):
    def make_user(self, status):
        return User.objects.create_user(
            email="member@example.com",
            password=PASSWORD,
            account_status=status,
            first_name="Mary",
        )

    def approve(self, user):
        user.account_status = User.AccountStatus.APPROVED
        with self.captureOnCommitCallbacks(execute=True):
            user.save(update_fields=["account_status"])
        user.refresh_from_db()

    def test_pending_to_approved_creates_one_notification_and_one_email(self):
        user = self.make_user(User.AccountStatus.PENDING)
        self.assertEqual(len(mail.outbox), 0)
        self.approve(user)

        self.assertEqual(user.account_status, User.AccountStatus.APPROVED)
        notifications = Notification.objects.filter(owner=user)
        self.assertEqual(notifications.count(), 1)
        notification = notifications.get()
        self.assertEqual(notification.title, "Application Approved")
        self.assertIn("GCTN/Apex Pay", notification.message)
        self.assertEqual(notification.event_type, Notification.EventType.MEMBERSHIP_APPROVED)
        self.assertEqual(notification.email_status, Notification.EmailStatus.SENT)
        self.assertIsNotNone(notification.email_sent_at)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])
        self.assertIn("GCTN/Apex Pay", mail.outbox[0].body)
        self.assertIn("approved", mail.outbox[0].subject.lower())

    def test_saving_an_already_approved_user_does_not_resend(self):
        user = self.make_user(User.AccountStatus.PENDING)
        self.approve(user)
        self.assertEqual(len(mail.outbox), 1)

        user.first_name = "Mary Jane"
        with self.captureOnCommitCallbacks(execute=True):
            user.save()
        self.assertEqual(Notification.objects.filter(owner=user).count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_general_user_promoted_directly_gets_no_approval_notification(self):
        user = self.make_user(User.AccountStatus.GENERAL)
        self.approve(user)
        self.assertEqual(Notification.objects.filter(owner=user).count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_non_staff_user_cannot_approve_through_admin(self):
        member = self.make_user(User.AccountStatus.PENDING)
        self.client.force_login(member)
        response = self.client.post(
            reverse("admin:core_user_change", args=[member.pk]),
            {
                "email": member.email,
                "account_status": User.AccountStatus.APPROVED,
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])
        member.refresh_from_db()
        self.assertEqual(member.account_status, User.AccountStatus.PENDING)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        MAILERS={
            "default": {
                "BACKEND": "core.test_notifications.FailingEmailBackend",
                "OPTIONS": {},
            }
        },
        DEFAULT_FROM_EMAIL="Gabsweb CashFlow <notifications@example.com>",
        PASSWORD_RESET_BASE_URL="https://cashflow.example.com",
    )
    def test_email_failure_keeps_approval_and_in_app_notification(self):
        user = self.make_user(User.AccountStatus.PENDING)
        self.approve(user)

        self.assertEqual(user.account_status, User.AccountStatus.APPROVED)
        notification = Notification.objects.get(owner=user)
        self.assertEqual(notification.email_status, Notification.EmailStatus.FAILED)
        self.assertEqual(notification.email_attempts, 1)
        self.assertIn("Email provider unavailable", notification.email_last_error)
        self.assertTrue(notification.message)

    def test_retry_does_not_duplicate_a_successful_email(self):
        from .notifications import send_notification_email

        user = self.make_user(User.AccountStatus.PENDING)
        self.approve(user)
        notification = Notification.objects.get(owner=user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(send_notification_email(notification.pk))
        self.assertEqual(len(mail.outbox), 1)


@override_settings(**LOCMEM_EMAIL)
class SavingsModalBalanceDistinctionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="saver@example.com", password=PASSWORD
        )
        self.client.force_login(self.user)

    def add_allocation(self, amount):
        budget = MonthlyBudget.objects.create(
            owner=self.user, month=date(2026, 11, 1)
        )
        category = BudgetCategory.objects.create(
            owner=self.user, name="Savings"
        )
        BudgetAllocation.objects.create(
            budget=budget,
            category=category,
            name="Savings",
            planned_amount=Decimal(amount),
            status=BudgetAllocation.Status.ACTIVE,
        )

    def withdraw(self, amount, day):
        return self.client.post(
            reverse("core:savings_withdrawals"),
            data=json.dumps(
                {"amount": amount, "date": day, "reason": "Test withdrawal"}
            ),
            content_type="application/json",
        )

    def test_informational_balance_includes_the_withdrawal_being_edited(self):
        self.add_allocation("100000.00")
        self.assertEqual(self.withdraw("20000.00", "2026-11-05").status_code, 201)
        second = self.withdraw("15000.00", "2026-11-15")
        self.assertEqual(second.status_code, 201)

        summary = self.client.get(
            reverse("core:savings_api"), {"month": "2026-11"}
        ).json()["summary"]
        self.assertEqual(summary["currentBalance"], "65000.00")

        withdrawal_id = second.json()["withdrawal"]["id"]
        replacement = self.client.post(
            reverse("core:savings_withdrawal_detail", args=[withdrawal_id]),
            data=json.dumps(
                {
                    "amount": "80000.00",
                    "date": "2026-11-18",
                    "reason": "Replacement within replacement availability",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(replacement.status_code, 200, replacement.content)
        summary = self.client.get(
            reverse("core:savings_api"), {"month": "2026-11"}
        ).json()["summary"]
        self.assertEqual(summary["currentBalance"], "0.00")
