from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from .models import Notification, User

PASSWORD = "StrongPass123!"


class PendingVerificationWorkflowTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            email="owner@example.com", password=PASSWORD
        )
        self.staff = User.objects.create_user(
            email="staff@example.com",
            password=PASSWORD,
            is_staff=True,
        )
        self.staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="core",
                codename="change_user",
            )
        )
        self.pending = self.make_user(
            "pending@example.com",
            User.AccountStatus.PENDING,
            first_name="Pending",
            last_name="Member",
            workplace_company=User.WorkplaceCompany.GCTN,
            professional_role="Analyst",
        )
        self.pending_no_workplace = self.make_user(
            "no-workplace@example.com",
            User.AccountStatus.PENDING,
        )
        self.pending_bad_workplace = self.make_user(
            "bad-workplace@example.com",
            User.AccountStatus.PENDING,
            workplace_company="OTHER",
        )
        self.pending_apex = self.make_user(
            "apex@example.com",
            User.AccountStatus.PENDING,
            workplace_company=User.WorkplaceCompany.APEX,
        )
        self.general = self.make_user(
            "general@example.com", User.AccountStatus.GENERAL
        )
        self.approved = self.make_user(
            "approved@example.com",
            User.AccountStatus.APPROVED,
            workplace_company=User.WorkplaceCompany.GCTN,
        )
        self.staff_pending = self.make_user(
            "staff-pending@example.com",
            User.AccountStatus.PENDING,
            is_staff=True,
            workplace_company=User.WorkplaceCompany.GCTN,
        )
        self.superuser_pending = User.objects.create_superuser(
            email="other-owner@example.com", password=PASSWORD
        )
        self.superuser_pending.account_status = User.AccountStatus.PENDING
        self.superuser_pending.save(update_fields=["account_status"])

        self.pending_url = reverse("admin:core_user_pending")

    def make_user(self, email, status, **extra):
        return User.objects.create_user(
            email=email,
            password=PASSWORD,
            account_status=status,
            **extra,
        )

    def approve_url(self, user):
        return reverse("admin:core_user_approve_pending", args=[user.pk])

    def return_url(self, user):
        return reverse("admin:core_user_return_pending", args=[user.pk])

    def login_superuser(self):
        self.client.force_login(self.superuser)

    def test_admin_logout_redirects_to_admin_login_only(self):
        self.login_superuser()
        response = self.client.post(reverse("admin:logout"))
        self.assertRedirects(
            response,
            reverse("admin:login"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            self.client.get(reverse("admin:index")).status_code,
            302,
        )

    def test_pending_page_requires_platform_superuser(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.pending_url)
        self.assertEqual(response.status_code, 403)

        self.login_superuser()
        response = self.client.get(self.pending_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending verifications")

    def test_pending_page_lists_only_eligible_applicants(self):
        self.login_superuser()
        response = self.client.get(self.pending_url)

        self.assertContains(response, self.pending.email)
        self.assertContains(response, self.pending_apex.email)
        self.assertNotContains(response, self.pending_no_workplace.email)
        self.assertNotContains(response, self.pending_bad_workplace.email)
        self.assertNotContains(response, self.general.email)
        self.assertNotContains(response, self.approved.email)
        self.assertNotContains(response, self.staff_pending.email)
        self.assertNotContains(response, self.superuser_pending.email)

    def test_pending_page_search_and_workplace_filter(self):
        self.login_superuser()
        response = self.client.get(self.pending_url, {"q": "Analyst"})
        self.assertContains(response, self.pending.email)
        self.assertNotContains(response, self.pending_apex.email)

        response = self.client.get(self.pending_url, {"workplace": "APEX"})
        self.assertContains(response, self.pending_apex.email)
        self.assertNotContains(response, self.pending.email)

    def test_approve_is_post_only(self):
        self.login_superuser()
        response = self.client.get(self.approve_url(self.pending))

        self.assertEqual(response.status_code, 405)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.account_status, User.AccountStatus.PENDING)

    def test_approve_sets_only_status_and_logs_without_notification(self):
        self.login_superuser()
        notification_count = Notification.objects.count()

        response = self.client.post(self.approve_url(self.pending), follow=True)

        self.assertEqual(response.status_code, 200)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.account_status, User.AccountStatus.APPROVED)
        self.assertEqual(
            self.pending.workplace_company, User.WorkplaceCompany.GCTN
        )
        self.assertEqual(self.pending.first_name, "Pending")
        self.assertEqual(Notification.objects.count(), notification_count)
        self.assertTrue(
            LogEntry.objects.filter(
                user=self.superuser,
                object_id=str(self.pending.pk),
                change_message__contains="Approved workplace access",
            ).exists()
        )

    def test_approve_rejects_missing_or_invalid_workplace(self):
        self.login_superuser()
        for target in (self.pending_no_workplace, self.pending_bad_workplace):
            response = self.client.post(self.approve_url(target), follow=True)

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "valid GCTN or Apex Pay workplace")
            target.refresh_from_db()
            self.assertEqual(
                target.account_status, User.AccountStatus.PENDING
            )
            self.assertFalse(
                LogEntry.objects.filter(object_id=str(target.pk)).exists()
            )

    def test_approve_rejects_non_pending_and_staff_applicants(self):
        self.login_superuser()
        for target in (self.general, self.staff_pending):
            response = self.client.post(self.approve_url(target), follow=True)

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "no longer a pending applicant")
            target.refresh_from_db()
            self.assertEqual(
                target.account_status,
                User.AccountStatus.GENERAL
                if target is self.general
                else User.AccountStatus.PENDING,
            )
            self.assertFalse(
                LogEntry.objects.filter(object_id=str(target.pk)).exists()
            )

    def test_return_confirmation_and_post_preserves_workplace(self):
        self.login_superuser()
        response = self.client.get(self.return_url(self.pending))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.pending.email)
        self.assertContains(response, "Return to General")

        response = self.client.post(self.return_url(self.pending), follow=True)

        self.assertEqual(response.status_code, 200)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.account_status, User.AccountStatus.GENERAL)
        self.assertEqual(
            self.pending.workplace_company, User.WorkplaceCompany.GCTN
        )
        self.assertTrue(
            LogEntry.objects.filter(
                user=self.superuser,
                object_id=str(self.pending.pk),
                change_message__contains="Returned",
            ).exists()
        )

    def test_return_rejects_non_pending_applicants(self):
        self.login_superuser()
        self.assertEqual(
            self.client.get(self.return_url(self.general)).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(self.return_url(self.general)).status_code,
            404,
        )

    def test_bulk_action_hardens_eligibility_workplace_and_notifications(self):
        self.login_superuser()
        notification_count = Notification.objects.count()

        response = self.client.post(
            reverse("admin:core_user_changelist"),
            {
                "action": "approve_membership",
                "_selected_action": [
                    str(self.pending.pk),
                    str(self.pending_no_workplace.pk),
                    str(self.general.pk),
                    str(self.staff_pending.pk),
                ],
                "index": "0",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.pending.refresh_from_db()
        self.pending_no_workplace.refresh_from_db()
        self.general.refresh_from_db()
        self.staff_pending.refresh_from_db()
        self.assertEqual(self.pending.account_status, User.AccountStatus.APPROVED)
        self.assertEqual(
            self.pending_no_workplace.account_status,
            User.AccountStatus.PENDING,
        )
        self.assertEqual(self.general.account_status, User.AccountStatus.GENERAL)
        self.assertEqual(
            self.staff_pending.account_status, User.AccountStatus.PENDING
        )
        self.assertEqual(Notification.objects.count(), notification_count)
        self.assertEqual(
            LogEntry.objects.filter(change_message__contains="Approved workplace access").count(),
            1,
        )
        self.assertContains(response, "approved")
        self.assertContains(response, "no valid")

    def test_bulk_action_rejects_staff_without_superuser(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("admin:core_user_changelist"),
            {
                "action": "approve_membership",
                "_selected_action": [str(self.pending.pk)],
                "index": "0",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.account_status, User.AccountStatus.PENDING)
        self.assertContains(response, "Only the platform superuser")
