import json

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Notification, PersonalShift, User, UserSupervisor

PASSWORD = "StrongPass123!"
LOCMEM_EMAIL = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "DEFAULT_FROM_EMAIL": "Gabsweb CashFlow <notifications@example.com>",
    "PASSWORD_RESET_BASE_URL": "https://cashflow.example.com",
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
}


def post_json(client, url, payload, method="post"):
    return getattr(client, method)(
        url,
        data=json.dumps(payload),
        content_type="application/json",
    )


class WorkplaceApiTestCase(TestCase):
    def make_user(self, email, status, **extra):
        return User.objects.create_user(
            email=email,
            password=PASSWORD,
            account_status=status,
            **extra,
        )


class WorkProfileApiTests(WorkplaceApiTestCase):
    def setUp(self):
        self.user = self.make_user("worker@example.com", User.AccountStatus.APPROVED)
        self.other = self.make_user("other@example.com", User.AccountStatus.APPROVED)
        self.client.force_login(self.user)

    def test_approved_user_can_save_work_details(self):
        response = post_json(
            self.client,
            reverse("core:work_profile_api"),
            {
                "work_team": "  Digital   Sales ",
                "joining_date": "2026-11-01",
                "joining_arrangement": " Afternoon shift ",
                "works_shifts": "on",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()["workProfile"]
        self.assertEqual(payload["workTeam"], "Digital Sales")
        self.assertEqual(payload["joiningDate"], "2026-11-01")
        self.assertEqual(payload["joiningArrangement"], "Afternoon shift")
        self.assertTrue(payload["worksShifts"])
        self.assertTrue(payload["hasApprovedAccess"])

        self.user.refresh_from_db()
        self.assertEqual(self.user.work_team, "Digital Sales")
        self.assertEqual(self.user.joining_arrangement, "Afternoon shift")
        self.assertTrue(self.user.works_shifts)
        self.other.refresh_from_db()
        self.assertEqual(self.other.work_team, "")
        self.assertIsNone(self.other.joining_date)

    def test_work_profile_read_is_private_to_the_authenticated_user(self):
        self.other.work_team = "Other Team"
        self.other.save(update_fields=["work_team"])
        response = self.client.get(reverse("core:work_profile_api"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workProfile"]["workTeam"], "")

    def test_general_user_cannot_save_joining_details(self):
        general = self.make_user(
            "general@example.com", User.AccountStatus.GENERAL
        )
        self.client.force_login(general)
        response = post_json(
            self.client,
            reverse("core:work_profile_api"),
            {"joining_date": "2026-11-01", "work_team": "General Team"},
        )
        self.assertEqual(response.status_code, 403)
        general.refresh_from_db()
        self.assertIsNone(general.joining_date)
        self.assertEqual(general.work_team, "")

    def test_pending_user_cannot_save_joining_details(self):
        pending = self.make_user(
            "pending@example.com",
            User.AccountStatus.PENDING,
            workplace_company=User.WorkplaceCompany.GCTN,
        )
        self.client.force_login(pending)
        response = post_json(
            self.client,
            reverse("core:work_profile_api"),
            {"joining_arrangement": "Night", "work_team": "Pending Team"},
        )
        self.assertEqual(response.status_code, 403)
        pending.refresh_from_db()
        self.assertEqual(pending.joining_arrangement, "")
        self.assertEqual(pending.work_team, "")

    def test_work_profile_requires_authentication(self):
        self.client.logout()
        response = self.client.get(reverse("core:work_profile_api"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:login"), response["Location"])


class SupervisorApiTests(WorkplaceApiTestCase):
    def setUp(self):
        self.user = self.make_user("worker@example.com", User.AccountStatus.APPROVED)
        self.other = self.make_user("other@example.com", User.AccountStatus.APPROVED)
        self.client.force_login(self.user)

    def test_approved_user_can_add_list_update_and_delete_supervisors(self):
        created = post_json(
            self.client, reverse("core:supervisors_api"), {"name": "  Ada  "}
        )
        self.assertEqual(created.status_code, 201, created.content)
        supervisor_id = created.json()["supervisor"]["id"]
        self.assertEqual(created.json()["supervisor"]["name"], "Ada")

        listed = self.client.get(reverse("core:supervisors_api"))
        self.assertEqual(
            [item["name"] for item in listed.json()["supervisors"]], ["Ada"]
        )

        updated = post_json(
            self.client,
            reverse("core:supervisor_detail", args=[supervisor_id]),
            {"name": "Ada Lovelace"},
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["supervisor"]["name"], "Ada Lovelace")

        deleted = self.client.delete(
            reverse("core:supervisor_detail", args=[supervisor_id])
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(UserSupervisor.objects.filter(owner=self.user).exists())

    def test_supervisors_are_isolated_between_users(self):
        UserSupervisor.objects.create(owner=self.other, name="Other Boss")
        response = self.client.get(reverse("core:supervisors_api"))
        self.assertEqual(response.json()["supervisors"], [])
        target = UserSupervisor.objects.get(owner=self.other)
        blocked = self.client.delete(
            reverse("core:supervisor_detail", args=[target.pk])
        )
        self.assertEqual(blocked.status_code, 404)
        self.assertTrue(UserSupervisor.objects.filter(pk=target.pk).exists())

    def test_duplicate_supervisor_name_is_rejected_case_insensitively(self):
        post_json(self.client, reverse("core:supervisors_api"), {"name": "Ada"})
        duplicate = post_json(
            self.client, reverse("core:supervisors_api"), {"name": "ada"}
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("name", duplicate.json()["errors"])
        self.assertEqual(UserSupervisor.objects.filter(owner=self.user).count(), 1)

    def test_general_and_pending_users_cannot_manage_supervisors(self):
        for status, email in (
            (User.AccountStatus.GENERAL, "general@example.com"),
            (User.AccountStatus.PENDING, "pending@example.com"),
        ):
            user = self.make_user(email, status)
            self.client.force_login(user)
            response = self.client.get(reverse("core:supervisors_api"))
            self.assertEqual(response.status_code, 403)
            created = post_json(
                self.client, reverse("core:supervisors_api"), {"name": "Boss"}
            )
            self.assertEqual(created.status_code, 403)
            self.assertFalse(UserSupervisor.objects.filter(owner=user).exists())


class PersonalShiftApiTests(WorkplaceApiTestCase):
    def setUp(self):
        self.user = self.make_user("general@example.com", User.AccountStatus.GENERAL)
        self.other = self.make_user("other@example.com", User.AccountStatus.GENERAL)
        self.client.force_login(self.user)

    def test_general_user_can_manage_personal_shifts(self):
        created = post_json(
            self.client,
            reverse("core:personal_shifts_api"),
            {
                "name": " Early ",
                "start_time": "06:00",
                "end_time": "14:00",
                "is_active": "on",
            },
        )
        self.assertEqual(created.status_code, 201, created.content)
        shift_id = created.json()["shift"]["id"]
        self.assertEqual(created.json()["shift"]["name"], "Early")

        listed = self.client.get(reverse("core:personal_shifts_api"))
        self.assertEqual(len(listed.json()["shifts"]), 1)

        updated = post_json(
            self.client,
            reverse("core:personal_shift_detail", args=[shift_id]),
            {
                "name": "Early",
                "start_time": "07:00",
                "end_time": "15:00",
                "is_active": "",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        shift = PersonalShift.objects.get(pk=shift_id)
        self.assertEqual(shift.start_time.strftime("%H:%M"), "07:00")
        self.assertFalse(shift.is_active)

        deleted = self.client.delete(
            reverse("core:personal_shift_detail", args=[shift_id])
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(PersonalShift.objects.filter(pk=shift_id).exists())

    def test_personal_shifts_are_isolated_between_users(self):
        PersonalShift.objects.create(
            owner=self.other,
            name="Night",
            start_time="22:00",
            end_time="06:00",
        )
        response = self.client.get(reverse("core:personal_shifts_api"))
        self.assertEqual(response.json()["shifts"], [])
        target = PersonalShift.objects.get(owner=self.other)
        blocked = self.client.delete(
            reverse("core:personal_shift_detail", args=[target.pk])
        )
        self.assertEqual(blocked.status_code, 404)
        self.assertTrue(PersonalShift.objects.filter(pk=target.pk).exists())

    def test_personal_shift_names_are_unique_per_owner(self):
        post_json(
            self.client,
            reverse("core:personal_shifts_api"),
            {"name": "Morning", "start_time": "06:00", "end_time": "14:00"},
        )
        duplicate = post_json(
            self.client,
            reverse("core:personal_shifts_api"),
            {"name": "morning", "start_time": "07:00", "end_time": "15:00"},
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("name", duplicate.json()["errors"])


@override_settings(**LOCMEM_EMAIL)
class RegistrationWelcomeTests(TestCase):
    def registration_payload(self, **overrides):
        payload = {
            "full_name": "Mary Jane Watson",
            "email": "mary@example.com",
            "password1": PASSWORD,
            "password2": PASSWORD,
            "workplace_access": "yes",
            "workplace_company": "APEX",
            "terms": "on",
        }
        payload.update(overrides)
        return payload

    def test_registration_stores_company_and_creates_one_welcome_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("core:register"), self.registration_payload()
            )
        self.assertRedirects(response, reverse("core:index"))
        user = User.objects.get(email="mary@example.com")
        self.assertEqual(user.workplace_company, User.WorkplaceCompany.APEX)
        self.assertEqual(user.account_status, User.AccountStatus.PENDING)

        notifications = Notification.objects.filter(owner=user)
        self.assertEqual(notifications.count(), 1)
        welcome = notifications.get()
        self.assertEqual(welcome.event_type, Notification.EventType.WELCOME)
        self.assertEqual(welcome.email_status, Notification.EmailStatus.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])
        self.assertIn("Welcome to Gabsweb CashFlow", mail.outbox[0].subject)
        self.assertNotIn(
            Notification.EventType.LOGIN,
            Notification.objects.filter(owner=user).values_list(
                "event_type", flat=True
            ),
        )

    def test_general_registration_does_not_keep_a_company(self):
        self.client.post(
            reverse("core:register"),
            self.registration_payload(
                email="general@example.com",
                workplace_access="no",
                workplace_company="GCTN",
            ),
        )
        user = User.objects.get(email="general@example.com")
        self.assertEqual(user.account_status, User.AccountStatus.GENERAL)
        self.assertEqual(user.workplace_company, "")


@override_settings(**LOCMEM_EMAIL)
class LoginNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="login@example.com", password=PASSWORD, first_name="Mary"
        )

    def login(self):
        return self.client.post(
            reverse("core:login"),
            {"email": self.user.email, "password": PASSWORD},
        )

    def test_each_real_login_creates_one_notification_and_one_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.login()
        self.client.post(reverse("core:logout"))
        with self.captureOnCommitCallbacks(execute=True):
            self.login()
        notifications = Notification.objects.filter(
            owner=self.user, event_type=Notification.EventType.LOGIN
        )
        self.assertEqual(notifications.count(), 2)
        self.assertEqual(
            notifications.filter(
                email_status=Notification.EmailStatus.SENT
            ).count(),
            2,
        )
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("New sign-in", mail.outbox[0].subject)

    def test_page_views_and_refreshes_do_not_create_login_notifications(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.login()
        self.assertEqual(
            Notification.objects.filter(
                owner=self.user, event_type=Notification.EventType.LOGIN
            ).count(),
            1,
        )
        self.client.get(reverse("core:index"))
        self.client.get(reverse("core:index"))
        self.client.get(reverse("core:settings"))
        self.assertEqual(
            Notification.objects.filter(
                owner=self.user, event_type=Notification.EventType.LOGIN
            ).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_failed_login_creates_no_notification(self):
        self.client.post(
            reverse("core:login"),
            {"email": self.user.email, "password": "WrongPassword!"},
        )
        self.assertFalse(
            Notification.objects.filter(
                owner=self.user, event_type=Notification.EventType.LOGIN
            ).exists()
        )


@override_settings(**LOCMEM_EMAIL)
class AdminApprovalActionTests(WorkplaceApiTestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="admin@example.com", password=PASSWORD
        )
        self.client.force_login(self.admin)

    def test_bulk_action_approves_only_pending_members(self):
        pending = self.make_user(
            "pending@example.com",
            User.AccountStatus.PENDING,
            workplace_company=User.WorkplaceCompany.GCTN,
        )
        general = self.make_user(
            "general@example.com", User.AccountStatus.GENERAL
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:core_user_changelist"),
                {
                    "action": "approve_membership",
                    "_selected_action": [str(pending.pk), str(general.pk)],
                    "index": "0",
                },
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        pending.refresh_from_db()
        general.refresh_from_db()
        self.assertEqual(pending.account_status, User.AccountStatus.APPROVED)
        self.assertEqual(general.account_status, User.AccountStatus.GENERAL)
        self.assertEqual(
            Notification.objects.filter(
                owner=pending,
                event_type=Notification.EventType.MEMBERSHIP_APPROVED,
            ).count(),
            0,
        )
        self.assertEqual(
            Notification.objects.filter(
                owner=general,
                event_type=Notification.EventType.MEMBERSHIP_APPROVED,
            ).count(),
            0,
        )
        self.assertContains(response, "approved")


class ProfileApiTests(WorkplaceApiTestCase):
    def setUp(self):
        self.first = User.objects.create_user(
            email="first-profile@example.com",
            password=PASSWORD,
            first_name="First",
            last_name="Person",
            professional_role="Accountant",
        )
        self.second = User.objects.create_user(
            email="second-profile@example.com",
            password=PASSWORD,
            first_name="Second",
            last_name="Person",
            professional_role="Developer",
        )

    def test_profile_api_returns_the_authenticated_users_actual_identity(self):
        self.client.force_login(self.first)
        response = self.client.get(reverse("core:profile_api"))
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["full_name"], "First Person")
        self.assertEqual(payload["email"], "first-profile@example.com")
        self.assertEqual(payload["professional_role"], "Accountant")

    def test_switching_accounts_returns_the_new_users_profile(self):
        self.client.force_login(self.first)
        first_payload = self.client.get(reverse("core:profile_api")).json()
        self.client.logout()
        self.client.force_login(self.second)
        second_payload = self.client.get(reverse("core:profile_api")).json()
        self.assertEqual(first_payload["email"], "first-profile@example.com")
        self.assertEqual(second_payload["email"], "second-profile@example.com")
        self.assertEqual(second_payload["full_name"], "Second Person")
        self.assertEqual(second_payload["professional_role"], "Developer")

    def test_profile_api_requires_authentication(self):
        response = self.client.get(reverse("core:profile_api"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:login"), response["Location"])

    def test_settings_page_exposes_the_profile_api_without_workplace_dependency(self):
        self.client.force_login(self.first)
        response = self.client.get(reverse("core:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-profile-url="' + reverse("core:profile_api") + '"')
        self.assertContains(response, 'data-url="' + reverse("core:work_profile_api") + '"')
        self.assertContains(response, "First")
        self.assertContains(response, "Accountant")
