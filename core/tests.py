import re
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.core import mail
from django.core.cache import cache
from django.core.mail.backends.base import BaseEmailBackend
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .admin import UserAdmin
from .forms import UserChangeForm, UserCreationForm
from .models import User


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class UserModelTests(TestCase):
    def test_user_is_the_configured_auth_model(self):
        self.assertIs(get_user_model(), User)
        self.assertEqual(User.USERNAME_FIELD, "email")
        self.assertNotIn("username", [field.name for field in User._meta.get_fields()])

    def test_user_can_be_created_with_email(self):
        user = User.objects.create_user(email="person@example.com", password="StrongPass123!")
        self.assertEqual(user.email, "person@example.com")
        self.assertTrue(user.check_password("StrongPass123!"))
        self.assertTrue(user.is_active)

    def test_email_is_required(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="StrongPass123!")

    def test_email_is_unique(self):
        User.objects.create_user(email="duplicate@example.com", password="StrongPass123!")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(email="duplicate@example.com", password="StrongPass123!")

    def test_email_is_stored_lowercase(self):
        user = User.objects.create_user(email="  Mixed.Case@Example.COM  ", password="StrongPass123!")
        self.assertEqual(user.email, "mixed.case@example.com")

    def test_first_display_name_returns_first_name_only(self):
        user = User.objects.create_user(
            email="names@example.com",
            password="StrongPass123!",
            first_name="Mary",
            last_name="Jane Watson",
        )
        self.assertEqual(user.get_first_display_name(), "Mary")

    def test_two_part_name_returns_first_two_names(self):
        user = User.objects.create_user(
            email="names@example.com",
            password="StrongPass123!",
            first_name="Mary",
            last_name="Jane Watson",
        )
        self.assertEqual(user.get_two_part_name(), "Mary Jane")

    def test_display_names_fall_back_to_email_local_part(self):
        user = User.objects.create_user(
            email="noname.person@example.com", password="StrongPass123!"
        )
        self.assertEqual(user.get_first_display_name(), "noname.person")
        self.assertEqual(user.get_two_part_name(), "noname.person")
        self.assertNotIn("@", user.get_first_display_name())
        self.assertNotIn("@", user.get_two_part_name())

    def test_display_names_fall_back_to_there_without_email(self):
        user = User(email="", first_name="", last_name="")
        self.assertEqual(user.get_first_display_name(), "there")
        self.assertEqual(user.get_two_part_name(), "there")

    def test_account_defaults_to_general(self):
        user = User.objects.create_user(email="general@example.com", password="StrongPass123!")
        self.assertEqual(user.account_status, User.AccountStatus.GENERAL)

    def test_pending_account_remains_ordinary_user(self):
        user = User.objects.create_user(
            email="pending@example.com",
            password="StrongPass123!",
            account_status=User.AccountStatus.PENDING,
        )
        self.assertEqual(user.account_status, User.AccountStatus.PENDING)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_approved_account_keeps_workplace_status(self):
        user = User.objects.create_user(
            email="approved@example.com",
            password="StrongPass123!",
            account_status=User.AccountStatus.APPROVED,
        )
        self.assertEqual(user.account_status, User.AccountStatus.APPROVED)
        self.assertFalse(user.is_staff)

    def test_ordinary_user_has_no_privileges(self):
        user = User.objects.create_user(email="ordinary@example.com", password="StrongPass123!")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_superuser_creation_via_manager(self):
        user = User.objects.create_superuser(email="owner@example.com", password="StrongPass123!")
        self.assertEqual(user.email, "owner@example.com")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)

    def test_superuser_requires_staff_and_superuser_flags(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(
                email="badstaff@example.com",
                password="StrongPass123!",
                is_staff=False,
            )
        with self.assertRaises(ValueError):
            User.objects.create_superuser(
                email="badsuper@example.com",
                password="StrongPass123!",
                is_superuser=False,
            )


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class UserAdminTests(TestCase):
    def test_admin_registers_custom_user_model(self):
        self.assertIn(User, admin.site._registry)
        self.assertIsInstance(admin.site._registry[User], UserAdmin)

    def test_admin_is_adapted_for_email_identity(self):
        self.assertIsInstance(admin.site._registry[User], BaseUserAdmin)
        self.assertEqual(UserAdmin.ordering, ("email",))
        self.assertIn("account_status", UserAdmin.list_display)
        self.assertIn("account_status", UserAdmin.list_filter)
        self.assertIn("email", UserAdmin.search_fields)
        self.assertIn("first_name", UserAdmin.search_fields)
        self.assertIn("last_name", UserAdmin.search_fields)

    def test_admin_forms_use_email_not_username(self):
        creation_form = UserCreationForm()
        self.assertIn("email", creation_form.fields)
        self.assertNotIn("username", creation_form.fields)

        change_form = UserChangeForm()
        self.assertIn("email", change_form.fields)
        self.assertNotIn("username", change_form.fields)

    def test_ordinary_user_cannot_access_admin(self):
        user = User.objects.create_user(email="plain@example.com", password="StrongPass123!")
        self.client.force_login(user)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_admin_add_change_and_list_pages_render(self):
        owner = User.objects.create_superuser(email="admin@example.com", password="StrongPass123!")
        member = User.objects.create_user(email="member@example.com", password="StrongPass123!")
        self.client.force_login(owner)

        add_response = self.client.get(reverse("admin:core_user_add"))
        self.assertEqual(add_response.status_code, 200)
        self.assertContains(add_response, 'name="email"')
        self.assertContains(add_response, 'name="account_status"')

        change_response = self.client.get(reverse("admin:core_user_change", args=[member.pk]))
        self.assertEqual(change_response.status_code, 200)
        self.assertContains(change_response, 'name="account_status"')

        list_response = self.client.get(reverse("admin:core_user_changelist"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "member@example.com")


PASSWORD = "V7x!pQ2mR9#L"
PROTECTED_ROUTE_NAMES = [
    "index",
    "transaction",
    "budgets",
    "savings",
    "goals",
    "work_schedule",
    "insights",
    "settings",
    "planner",
]
PUBLIC_ROUTE_NAMES = [
    "login",
    "register",
    "password_reset",
    "password_reset_done",
    "password_reset_confirm",
    "password_reset_complete",
    "terms",
    "privacy_policy",
    "bad_request",
    "permission_denied",
    "page_not_found",
    "server_error",
]


def registration_payload(**overrides):
    payload = {
        "full_name": "Mary Jane Watson",
        "email": "mary@example.com",
        "password1": PASSWORD,
        "password2": PASSWORD,
        "workplace_access": "no",
        "terms": "on",
    }
    payload.update(overrides)
    return payload


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class RegistrationTests(TestCase):
    def test_general_registration_creates_logged_in_general_user(self):
        response = self.client.post(reverse("core:register"), registration_payload())
        self.assertRedirects(response, reverse("core:index"))

        user = User.objects.get(email="mary@example.com")
        self.assertEqual(user.account_status, User.AccountStatus.GENERAL)
        self.assertEqual(user.first_name, "Mary")
        self.assertEqual(user.last_name, "Jane Watson")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_pending_registration_creates_logged_in_pending_user(self):
        response = self.client.post(
            reverse("core:register"),
            registration_payload(email="pending@example.com", workplace_access="yes"),
        )
        self.assertRedirects(response, reverse("core:index"))

        user = User.objects.get(email="pending@example.com")
        self.assertEqual(user.account_status, User.AccountStatus.PENDING)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_pending_registration_does_not_add_a_duplicate_success_message(self):
        response = self.client.post(
            reverse("core:register"),
            registration_payload(email="pending@example.com", workplace_access="yes"),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Your account was created")
        self.assertContains(response, "Workplace access pending")

    def test_general_registration_shows_the_success_message(self):
        response = self.client.post(
            reverse("core:register"),
            registration_payload(email="general@example.com", workplace_access="no"),
            follow=True,
        )
        self.assertContains(response, "Your account was created and you are now signed in.")

    def test_invalid_email_is_rejected(self):
        response = self.client.post(
            reverse("core:register"),
            registration_payload(email="not-an-email"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid email address")
        self.assertFalse(User.objects.filter(email="not-an-email").exists())

    def test_duplicate_email_is_rejected(self):
        User.objects.create_user(email="existing@example.com", password=PASSWORD)
        response = self.client.post(
            reverse("core:register"),
            registration_payload(email="Existing@Example.com"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email address already exists")
        self.assertEqual(User.objects.filter(email__iexact="existing@example.com").count(), 1)

    def test_password_mismatch_is_rejected(self):
        response = self.client.post(
            reverse("core:register"),
            registration_payload(password2="DifferentPass123!"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "didn’t match")
        self.assertFalse(User.objects.filter(email="mary@example.com").exists())

    def test_password_validation_is_enforced(self):
        response = self.client.post(
            reverse("core:register"),
            registration_payload(password1="12345678", password2="12345678"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="mary@example.com").exists())
        self.assertGreaterEqual(len(response.context["form"].errors["password2"]), 1)

    def test_registration_cannot_submit_approved_status_or_privileges(self):
        response = self.client.post(
            reverse("core:register"),
            registration_payload(
                workplace_access="no",
                account_status="APPROVED",
                is_staff="on",
                is_superuser="on",
            ),
        )
        self.assertRedirects(response, reverse("core:index"))

        user = User.objects.get(email="mary@example.com")
        self.assertEqual(user.account_status, User.AccountStatus.GENERAL)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_workplace_yes_cannot_submit_approved_status(self):
        self.client.post(
            reverse("core:register"),
            registration_payload(
                workplace_access="yes",
                account_status="APPROVED",
                is_staff="on",
                is_superuser="on",
            ),
        )
        user = User.objects.get(email="mary@example.com")
        self.assertEqual(user.account_status, User.AccountStatus.PENDING)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_single_word_name_is_handled(self):
        self.client.post(
            reverse("core:register"),
            registration_payload(full_name="Madonna"),
        )
        user = User.objects.get(email="mary@example.com")
        self.assertEqual(user.first_name, "Madonna")
        self.assertEqual(user.last_name, "")

    def test_register_page_contains_workplace_question_and_csrf(self):
        response = self.client.get(reverse("core:register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Do you work at GCTN or Apex Pay?")
        self.assertContains(response, 'name="workplace_access"')
        self.assertContains(response, 'name="full_name"')
        self.assertContains(response, 'name="password1"')
        self.assertContains(response, 'name="password2"')
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_authenticated_user_is_redirected_away_from_register(self):
        user = User.objects.create_user(email="signed@example.com", password=PASSWORD)
        self.client.force_login(user)
        response = self.client.get(reverse("core:register"))
        self.assertRedirects(response, reverse("core:index"))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class LoginLogoutTests(TestCase):
    def setUp(self):
        self.general = User.objects.create_user(
            email="general@example.com",
            password=PASSWORD,
            first_name="General",
            last_name="User",
        )
        self.pending = User.objects.create_user(
            email="pending@example.com",
            password=PASSWORD,
            account_status=User.AccountStatus.PENDING,
        )
        self.approved = User.objects.create_user(
            email="approved@example.com",
            password=PASSWORD,
            account_status=User.AccountStatus.APPROVED,
        )
        self.inactive = User.objects.create_user(
            email="inactive@example.com",
            password=PASSWORD,
            is_active=False,
        )

    def test_valid_email_password_login_creates_session(self):
        response = self.client.post(
            reverse("core:login"),
            {"email": "general@example.com", "password": PASSWORD},
        )
        self.assertRedirects(response, reverse("core:index"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.general.pk)

    def test_registered_email_with_wrong_password_says_password_is_incorrect(self):
        response = self.client.post(
            reverse("core:login"),
            {"email": "general@example.com", "password": "WrongPassword!"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The password you entered is incorrect.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unknown_email_produces_separate_message(self):
        response = self.client.post(
            reverse("core:login"),
            {"email": "missing@example.com", "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This email is not registered.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_registered_email_with_incorrect_password(self):
        response = self.client.post(
            reverse("core:login"),
            {"email": "general@example.com", "password": "WrongPassword!"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The password you entered is incorrect.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_pending_user_can_log_in(self):
        response = self.client.post(
            reverse("core:login"),
            {"email": "pending@example.com", "password": PASSWORD},
        )
        self.assertRedirects(response, reverse("core:index"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.pending.pk)

    def test_approved_user_can_log_in(self):
        response = self.client.post(
            reverse("core:login"),
            {"email": "approved@example.com", "password": PASSWORD},
        )
        self.assertRedirects(response, reverse("core:index"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.approved.pk)

    def test_inactive_user_cannot_log_in(self):
        response = self.client.post(
            reverse("core:login"),
            {"email": "inactive@example.com", "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This account is inactive.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_remember_me_extends_session_expiry(self):
        self.client.post(
            reverse("core:login"),
            {"email": "general@example.com", "password": PASSWORD, "remember_me": "on"},
        )
        self.assertGreater(self.client.session.get_expiry_age(), 1000000)

    def test_post_logout_clears_session(self):
        self.client.force_login(self.general)
        response = self.client.post(reverse("core:logout"))
        self.assertRedirects(response, reverse("core:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_message_renders_as_an_auto_dismissing_toast(self):
        self.client.force_login(self.general)
        response = self.client.post(reverse("core:logout"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="auth-toast-region"')
        self.assertContains(response, "You have been signed out.")
        self.assertContains(response, "data-auto-dismiss")

    def test_get_logout_is_not_allowed(self):
        self.client.force_login(self.general)
        response = self.client.get(reverse("core:logout"))
        self.assertEqual(response.status_code, 405)

    def test_logout_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.general)

        blocked = client.post(reverse("core:logout"))
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("_auth_user_id", client.session)

        page = client.get(reverse("core:index"))
        token = page.cookies["csrftoken"].value
        allowed = client.post(
            reverse("core:logout"),
            {"csrfmiddlewaretoken": token},
        )
        self.assertRedirects(allowed, reverse("core:login"))
        self.assertNotIn("_auth_user_id", client.session)

    def test_logout_controls_are_native_form_submissions(self):
        self.client.force_login(self.general)
        response = self.client.get(reverse("core:index"))
        self.assertContains(response, 'id="logout-form"')
        self.assertContains(response, 'method="post"', count=1)
        self.assertContains(response, 'action="%s"' % reverse("core:logout"))
        self.assertContains(response, 'form="logout-form"')
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_protected_routes_require_authentication_after_logout(self):
        self.client.force_login(self.general)
        self.client.post(reverse("core:logout"))

        for name in PROTECTED_ROUTE_NAMES:
            url = reverse(f"core:{name}")
            with self.subTest(name=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("core:login")))

    def test_authenticated_user_is_redirected_away_from_login(self):
        self.client.force_login(self.general)
        response = self.client.get(reverse("core:login"))
        self.assertRedirects(response, reverse("core:index"))

    def test_login_page_contains_form_and_csrf(self):
        response = self.client.get(reverse("core:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, "csrfmiddlewaretoken")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class WorkplaceAccessTests(TestCase):
    def create_user(self, email, status):
        return User.objects.create_user(
            email=email,
            password=PASSWORD,
            account_status=status,
        )

    def test_context_flag_matches_account_status(self):
        cases = [
            (User.AccountStatus.GENERAL, False),
            (User.AccountStatus.PENDING, False),
            (User.AccountStatus.APPROVED, True),
        ]
        for index, (status, expected) in enumerate(cases):
            user = self.create_user(f"status{index}@example.com", status)
            self.client.force_login(user)
            response = self.client.get(reverse("core:index"))
            self.assertEqual(
                response.context["can_access_workplace_schedule"],
                expected,
            )
            self.assertContains(
                response,
                "window.GABSWEB_WORKPLACE_ACCESS = "
                + ("true" if expected else "false")
                + ";",
            )

    def test_workplace_schedule_sections_are_hidden_for_unapproved_users(self):
        for index, status in enumerate(
            [User.AccountStatus.GENERAL, User.AccountStatus.PENDING]
        ):
            user = self.create_user(f"unapproved{index}@example.com", status)
            self.client.force_login(user)
            response = self.client.get(reverse("core:work_schedule"))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["can_access_workplace_schedule"])
            self.assertContains(
                response, "window.GABSWEB_WORKPLACE_ACCESS = false;"
            )
            self.assertNotContains(response, "Automatic schedule generation")
            self.assertNotContains(response, "Work rotation settings")
            self.assertNotContains(response, 'id="ws-rotation-form"')
            self.assertNotContains(response, 'id="ws-generation-card"')
            self.assertContains(response, "Add schedule entry")

    def test_unapproved_users_cannot_unlock_workplace_access_with_request_data(self):
        for index, status in enumerate(
            [User.AccountStatus.GENERAL, User.AccountStatus.PENDING]
        ):
            user = self.create_user(f"crafted{index}@example.com", status)
            self.client.force_login(user)

            query_response = self.client.get(
                reverse("core:work_schedule"),
                {
                    "account_status": "APPROVED",
                    "workplace_access": "yes",
                    "approved": "1",
                },
            )
            self.assertFalse(
                query_response.context["can_access_workplace_schedule"]
            )
            self.assertNotContains(query_response, "Work rotation settings")

            post_response = self.client.post(
                reverse("core:work_schedule"),
                {
                    "account_status": "APPROVED",
                    "workplace_access": "yes",
                    "approved": "1",
                },
            )
            self.assertFalse(
                post_response.context["can_access_workplace_schedule"]
            )
            self.assertNotContains(post_response, "Automatic schedule generation")
            self.assertNotContains(post_response, "Work rotation settings")

    def test_workplace_schedule_sections_render_for_approved_users(self):
        user = self.create_user("approved@example.com", User.AccountStatus.APPROVED)
        self.client.force_login(user)
        response = self.client.get(reverse("core:work_schedule"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_access_workplace_schedule"])
        self.assertContains(response, "window.GABSWEB_WORKPLACE_ACCESS = true;")
        self.assertContains(response, "Automatic schedule generation")
        self.assertContains(response, "Work rotation settings")
        self.assertContains(response, 'id="ws-generation-card"')
        self.assertContains(response, 'id="ws-rotation-form"')

    def test_workplace_access_is_evaluated_per_user(self):
        approved = self.create_user("owner-approved@example.com", User.AccountStatus.APPROVED)
        general = self.create_user("separate-general@example.com", User.AccountStatus.GENERAL)

        approved_client = Client()
        approved_client.force_login(approved)
        general_client = Client()
        general_client.force_login(general)

        approved_response = approved_client.get(reverse("core:work_schedule"))
        general_response = general_client.get(reverse("core:work_schedule"))

        self.assertTrue(approved_response.context["can_access_workplace_schedule"])
        self.assertContains(approved_response, "Work rotation settings")
        self.assertFalse(general_response.context["can_access_workplace_schedule"])
        self.assertNotContains(general_response, "Work rotation settings")

    def test_revoked_approval_removes_workplace_access_on_next_request(self):
        user = self.create_user("revoked@example.com", User.AccountStatus.APPROVED)
        self.client.force_login(user)
        approved_response = self.client.get(reverse("core:work_schedule"))
        self.assertContains(approved_response, "Work rotation settings")

        User.objects.filter(pk=user.pk).update(account_status=User.AccountStatus.GENERAL)
        revoked_response = self.client.get(reverse("core:work_schedule"))
        self.assertFalse(revoked_response.context["can_access_workplace_schedule"])
        self.assertNotContains(revoked_response, "Work rotation settings")
        self.assertNotContains(revoked_response, "Automatic schedule generation")

    def test_shared_routes_remain_available_to_every_status(self):
        for index, status in enumerate(
            [
                User.AccountStatus.GENERAL,
                User.AccountStatus.PENDING,
                User.AccountStatus.APPROVED,
            ]
        ):
            user = self.create_user(f"shared{index}@example.com", status)
            self.client.force_login(user)
            for name in ["index", "budgets", "savings", "work_schedule", "planner"]:
                with self.subTest(status=status, route=name):
                    response = self.client.get(reverse(f"core:{name}"))
                    self.assertEqual(response.status_code, 200)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AccessControlTests(TestCase):
    def test_all_application_routes_require_authentication(self):
        for name in PROTECTED_ROUTE_NAMES:
            url = reverse(f"core:{name}")
            with self.subTest(name=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("core:login")))
                self.assertIn(f"next={url}", response["Location"])

    def test_public_routes_remain_accessible(self):
        for name in PUBLIC_ROUTE_NAMES:
            url = reverse(f"core:{name}")
            with self.subTest(name=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_all_application_routes_work_after_login(self):
        user = User.objects.create_user(email="member@example.com", password=PASSWORD)
        self.client.force_login(user)
        for name in PROTECTED_ROUTE_NAMES:
            url = reverse(f"core:{name}")
            with self.subTest(name=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_intended_destination_is_used_after_login(self):
        user = User.objects.create_user(email="member@example.com", password=PASSWORD)
        response = self.client.get(reverse("core:budgets"))
        self.assertIn(f"next={reverse('core:budgets')}", response["Location"])

        response = self.client.post(
            reverse("core:login"),
            {
                "email": user.email,
                "password": PASSWORD,
                "next": reverse("core:budgets"),
            },
        )
        self.assertRedirects(response, reverse("core:budgets"))

    def test_unsafe_external_next_is_rejected(self):
        user = User.objects.create_user(email="member@example.com", password=PASSWORD)
        response = self.client.post(
            reverse("core:login"),
            {
                "email": user.email,
                "password": PASSWORD,
                "next": "https://evil.example.com/steal",
            },
        )
        self.assertRedirects(response, reverse("core:index"))

    def test_approved_status_is_recognized_on_next_request(self):
        user = User.objects.create_user(
            email="pending@example.com",
            password=PASSWORD,
            account_status=User.AccountStatus.PENDING,
        )
        self.client.force_login(user)

        pending_response = self.client.get(reverse("core:index"))
        self.assertContains(pending_response, "Workplace access pending")
        self.assertContains(pending_response, "Your request to access GCTN/Apex Pay workplace features")
        self.assertContains(pending_response, "Contact the platform owner if you need assistance")
        self.assertEqual(
            pending_response.context["user"].account_status,
            User.AccountStatus.PENDING,
        )

        User.objects.filter(pk=user.pk).update(account_status=User.AccountStatus.APPROVED)
        approved_response = self.client.get(reverse("core:index"))
        self.assertNotContains(approved_response, "Workplace access pending.")
        self.assertEqual(
            approved_response.context["user"].account_status,
            User.AccountStatus.APPROVED,
        )

    def test_revoked_workplace_approval_is_recognized(self):
        user = User.objects.create_user(
            email="approved@example.com",
            password=PASSWORD,
            account_status=User.AccountStatus.APPROVED,
        )
        self.client.force_login(user)
        User.objects.filter(pk=user.pk).update(account_status=User.AccountStatus.GENERAL)
        response = self.client.get(reverse("core:index"))
        self.assertEqual(
            response.context["user"].account_status,
            User.AccountStatus.GENERAL,
        )
        self.assertNotContains(response, "Workplace access pending.")

    def test_authenticated_name_and_shared_layout_render(self):
        user = User.objects.create_user(
            email="member@example.com",
            password=PASSWORD,
            first_name="Mary",
            last_name="Watson",
        )
        self.client.force_login(user)
        response = self.client.get(reverse("core:index"))
        self.assertContains(response, "Welcome back, Mary!")
        self.assertContains(response, 'id="sidebar"')
        self.assertContains(response, 'id="header-welcome"')
        self.assertContains(response, 'data-user-first-name="Mary"')
        self.assertContains(response, 'data-user-two-part-name="Mary Watson"')
        self.assertContains(response, 'data-user-full-name="Mary Watson"')
        self.assertContains(response, 'data-user-email="member@example.com"')
        self.assertContains(response, 'id="logout-form"')

    def test_superuser_without_name_uses_email_local_part(self):
        user = User.objects.create_superuser(
            email="gabrielonje@gmail.com", password=PASSWORD
        )
        self.client.force_login(user)
        response = self.client.get(reverse("core:index"))
        self.assertContains(response, 'data-user-first-name="gabrielonje"')
        self.assertContains(response, 'data-user-two-part-name="gabrielonje"')
        self.assertContains(
            response,
            '<h2 id="sidebar-user-name" class="text-sm font-semibold text-text truncate">gabrielonje</h2>',
            html=True,
        )

    def test_sidebar_uses_first_two_names(self):
        user = User.objects.create_user(
            email="member@example.com",
            password=PASSWORD,
            first_name="Mary",
            last_name="Jane Watson",
        )
        self.client.force_login(user)
        response = self.client.get(reverse("core:index"))
        self.assertContains(
            response,
            '<h2 id="sidebar-user-name" class="text-sm font-semibold text-text truncate">Mary Jane</h2>',
            html=True,
        )
        self.assertContains(response, "Welcome back, Mary!")
        self.assertNotContains(response, "Welcome back, Mary Jane")

    def test_profile_update_saves_names_and_email(self):
        user = User.objects.create_user(
            email="profile@example.com", password=PASSWORD
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("core:update_profile"),
            {"name": "Mary Jane Watson", "email": "mary@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["full_name"], "Mary Jane Watson")
        self.assertEqual(payload["first_name"], "Mary")
        self.assertEqual(payload["two_part_name"], "Mary Jane")

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Mary")
        self.assertEqual(user.last_name, "Jane Watson")
        self.assertEqual(user.email, "mary@example.com")

    def test_profile_update_rejects_invalid_email_and_duplicate(self):
        user = User.objects.create_user(
            email="profile@example.com", password=PASSWORD
        )
        User.objects.create_user(email="taken@example.com", password=PASSWORD)
        self.client.force_login(user)
        invalid = self.client.post(
            reverse("core:update_profile"),
            {"name": "General User", "email": "not-an-email"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("email", invalid.json()["errors"])

        duplicate = self.client.post(
            reverse("core:update_profile"),
            {"name": "General User", "email": "taken@example.com"},
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("email", duplicate.json()["errors"])

    def test_profile_update_requires_authentication_and_post(self):
        anonymous = self.client.post(
            reverse("core:update_profile"),
            {"name": "Anon User", "email": "anon@example.com"},
        )
        self.assertEqual(anonymous.status_code, 302)

        user = User.objects.create_user(
            email="profile@example.com", password=PASSWORD
        )
        self.client.force_login(user)
        get_response = self.client.get(reverse("core:update_profile"))
        self.assertEqual(get_response.status_code, 405)

    def test_existing_route_names_are_preserved(self):
        for name in PROTECTED_ROUTE_NAMES + PUBLIC_ROUTE_NAMES + ["logout"]:
            with self.subTest(name=name):
                self.assertTrue(reverse(f"core:{name}").startswith("/"))

    def test_csrf_is_enforced_on_login(self):
        user = User.objects.create_user(email="csrf@example.com", password=PASSWORD)
        client = Client(enforce_csrf_checks=True)

        blocked = client.post(
            reverse("core:login"),
            {"email": user.email, "password": PASSWORD},
        )
        self.assertEqual(blocked.status_code, 403)

        page = client.get(reverse("core:login"))
        token = page.cookies["csrftoken"].value
        allowed = client.post(
            reverse("core:login"),
            {
                "email": user.email,
                "password": PASSWORD,
                "csrfmiddlewaretoken": token,
            },
        )
        self.assertEqual(allowed.status_code, 302)


class FailingEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise RuntimeError("Email delivery failed for the test.")


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
)
class PasswordResetWorkflowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="reset@example.com",
            password=PASSWORD,
            first_name="Mary",
            last_name="Jane Watson",
            account_status=User.AccountStatus.PENDING,
        )

    def request_reset(self, email="reset@example.com", follow=False):
        return self.client.post(
            reverse("core:password_reset"), {"email": email}, follow=follow
        )

    def extract_reset_path(self, message):
        match = re.search(r"https?://[^\s]+/password-reset-confirm/[^\s]+", message.body)
        self.assertIsNotNone(match, "Password reset link missing from the email body.")
        return urlparse(match.group(0)).path

    def open_reset_form(self, path):
        response = self.client.get(path, follow=True)
        self.assertContains(response, "Create a New Password")
        return response.request["PATH_INFO"]

    def test_request_page_is_public_and_uses_csrf(self):
        response = self.client.get(reverse("core:password_reset"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_registered_account_receives_reset_email(self):
        response = self.request_reset()
        self.assertRedirects(response, reverse("core:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["reset@example.com"])
        self.assertEqual(message.from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(message.subject, "Reset your Gabsweb CashFlow password")
        self.assertIn("/password-reset-confirm/", message.body)
        self.assertTrue(
            any(content_type == "text/html" for _, content_type in message.alternatives)
        )
        html_body = message.alternatives[0][0]
        self.assertIn("/password-reset-confirm/", html_body)
        self.assertIn("expires in 1 hour", html_body)

    def test_active_normalized_accounts_are_eligible_for_password_reset(self):
        from .forms import GabswebPasswordResetForm

        user = User.objects.create_user(
            email="  Original.User@Example.COM  ",
            password=PASSWORD,
        )
        form = GabswebPasswordResetForm(
            {"email": " original.user@example.com "}
        )
        self.assertTrue(form.is_valid())
        self.assertIn(
            user,
            list(form.get_users("original.user@example.com")),
        )
        self.assertEqual(user.email, "original.user@example.com")
        self.assertTrue(user.is_active)
        self.assertTrue(user.has_usable_password())

    def test_active_accounts_in_every_workplace_status_receive_reset_email(self):
        for status in (
            User.AccountStatus.GENERAL,
            User.AccountStatus.PENDING,
            User.AccountStatus.APPROVED,
        ):
            user = User.objects.create_user(
                email=f"{status.lower()}@example.com",
                password=PASSWORD,
                account_status=status,
            )
            mail.outbox.clear()
            response = self.request_reset(email=user.email)
            self.assertRedirects(response, reverse("core:password_reset_done"))
            self.assertEqual(len(mail.outbox), 1)
            self.assertEqual(mail.outbox[0].to, [user.email])

    def test_html_email_uses_the_configured_absolute_logo_url(self):
        logo_url = (
            "https://cashflow.gabsweb.com.ng/static/assets/images/"
            "gabsweb-cashflow-logo.png"
        )
        with self.settings(EMAIL_LOGO_URL=logo_url):
            self.request_reset()
        html_body = mail.outbox[0].alternatives[0][0]
        self.assertIn(f'src="{logo_url}"', html_body)
        self.assertIn('alt="Gabsweb CashFlow logo"', html_body)

    def test_html_email_uses_text_fallback_when_logo_is_not_configured(self):
        with self.settings(EMAIL_LOGO_URL=""):
            self.request_reset()
        html_body = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("<img", html_body)
        self.assertNotIn("data:image", html_body)
        self.assertIn("Gabsweb CashFlow</span>", html_body)

    def test_plain_text_email_is_available(self):
        self.request_reset()
        message = mail.outbox[0]
        self.assertIn("Reset Your Password", message.body)
        self.assertIn("/password-reset-confirm/", message.body)
        self.assertNotIn("<html", message.body)
        self.assertNotIn("<img", message.body)

    def test_email_templates_do_not_embed_images_or_private_hosts(self):
        from pathlib import Path

        templates = Path(settings.BASE_DIR) / "core/templates/templates/emails"
        for name in (
            "password_reset_email.html",
            "password_reset_email.txt",
            "password_reset_subject.txt",
        ):
            source = (templates / name).read_text(encoding="utf-8")
            self.assertNotIn("data:image", source)
            self.assertNotIn("localhost", source)
            self.assertNotIn("127.0.0.1", source)
            self.assertNotIn("10.183.70.248", source)
            self.assertNotIn("192.168.", source)

    def test_unregistered_email_gets_identical_public_response(self):
        registered = self.request_reset()
        mail.outbox.clear()
        unregistered = self.request_reset("missing@example.com")
        self.assertEqual(registered.status_code, unregistered.status_code)
        self.assertEqual(registered["Location"], unregistered["Location"])
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_account_does_not_receive_email(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.request_reset()
        self.assertRedirects(response, reverse("core:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_email_format_is_rejected_without_sending(self):
        response = self.client.post(
            reverse("core:password_reset"), {"email": "not-an-email"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid email address")
        self.assertEqual(len(mail.outbox), 0)

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            reverse("core:password_reset"), {"email": "reset@example.com"}
        )
        self.assertEqual(response.status_code, 403)

    def test_valid_token_resets_password_and_old_password_stops_working(self):
        self.request_reset()
        path = self.extract_reset_path(mail.outbox[0])
        form_path = self.open_reset_form(path)

        response = self.client.post(
            form_path,
            {
                "new_password1": "NewStrong!234",
                "new_password2": "NewStrong!234",
            },
        )
        self.assertRedirects(
            response, reverse("core:password_reset_complete")
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewStrong!234"))
        self.assertFalse(self.user.check_password(PASSWORD))
        self.assertIsNotNone(
            authenticate(
                email="reset@example.com", password="NewStrong!234"
            )
        )
        self.assertIsNone(
            authenticate(email="reset@example.com", password=PASSWORD)
        )

    def test_used_token_cannot_be_reused(self):
        self.request_reset()
        path = self.extract_reset_path(mail.outbox[0])
        form_path = self.open_reset_form(path)
        self.client.post(
            form_path,
            {
                "new_password1": "NewStrong!234",
                "new_password2": "NewStrong!234",
            },
        )
        reuse = self.client.get(path)
        self.assertContains(reuse, "invalid or has expired")

    def test_invalid_token_shows_invalid_link_state(self):
        self.request_reset()
        path = self.extract_reset_path(mail.outbox[0])
        invalid_path = path.replace(
            path.rstrip("/").split("/")[-1], "invalid-token"
        )
        response = self.client.get(invalid_path)
        self.assertContains(response, "invalid or has expired")
        self.assertContains(response, "Request Another Link")

    def test_expired_token_shows_invalid_link_state(self):
        self.request_reset()
        path = self.extract_reset_path(mail.outbox[0])
        with self.settings(PASSWORD_RESET_TIMEOUT=-1):
            response = self.client.get(path)
        self.assertContains(response, "invalid or has expired")

    def test_weak_password_is_rejected_and_old_password_remains(self):
        self.request_reset()
        path = self.extract_reset_path(mail.outbox[0])
        form_path = self.open_reset_form(path)
        response = self.client.post(
            form_path,
            {
                "new_password1": "12345678901",
                "new_password2": "12345678901",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_password_reset_preserves_account_status_and_profile(self):
        self.request_reset()
        path = self.extract_reset_path(mail.outbox[0])
        form_path = self.open_reset_form(path)
        self.client.post(
            form_path,
            {
                "new_password1": "NewStrong!234",
                "new_password2": "NewStrong!234",
            },
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, User.AccountStatus.PENDING)
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertEqual(self.user.first_name, "Mary")
        self.assertEqual(self.user.last_name, "Jane Watson")
        self.assertEqual(self.user.email, "reset@example.com")

    def test_repeated_requests_are_throttled(self):
        with self.assertLogs("core.views", level="WARNING") as logs:
            for _ in range(6):
                self.request_reset()
        self.assertEqual(len(mail.outbox), 5)
        self.assertIn("rate limit reached", "\n".join(logs.output))

    def test_successful_submission_is_logged(self):
        with self.assertLogs("core.forms", level="INFO") as logs:
            self.request_reset()
        self.assertIn("accepted by the mail server", "\n".join(logs.output))

    def test_email_delivery_failure_is_handled_safely(self):
        with self.settings(
            MAILERS={"default": {"BACKEND": "core.tests.FailingEmailBackend"}}
        ):
            with self.assertLogs("core.forms", level="ERROR") as logs:
                response = self.request_reset()
        self.assertRedirects(response, reverse("core:password_reset_done"))
        output = "\n".join(logs.output)
        self.assertIn("Password reset email delivery failed", output)
        self.assertIn("type=RuntimeError", output)
        self.assertNotIn(self.user.email, output)

    def test_connection_refused_is_retried_once(self):
        from unittest.mock import patch

        refused = ConnectionRefusedError(111, "Connection refused")
        with patch(
            "core.forms.EmailMultiAlternatives.send",
            side_effect=[refused, 1],
        ) as send:
            with patch("core.forms.time.sleep") as sleep:
                with self.assertLogs("core.forms", level="WARNING") as logs:
                    response = self.request_reset()

        self.assertRedirects(response, reverse("core:password_reset_done"))
        self.assertEqual(send.call_count, 2)
        sleep.assert_called_once_with(1.0)
        output = "\n".join(logs.output)
        self.assertIn("connection failed", output)
        self.assertIn("type=ConnectionRefusedError", output)
        self.assertIn("retrying once", output)
        self.assertNotIn(self.user.email, output)

    def test_persistent_connection_refused_logs_failure_after_retry(self):
        from unittest.mock import patch

        refused = ConnectionRefusedError(111, "Connection refused")
        with patch(
            "core.forms.EmailMultiAlternatives.send",
            side_effect=refused,
        ) as send:
            with patch("core.forms.time.sleep"):
                with self.assertLogs("core.forms", level="ERROR") as logs:
                    response = self.request_reset()

        self.assertRedirects(response, reverse("core:password_reset_done"))
        self.assertEqual(send.call_count, 2)
        output = "\n".join(logs.output)
        self.assertIn("Password reset email delivery failed", output)
        self.assertIn("type=ConnectionRefusedError", output)
        self.assertIn("errno=111", output)
        self.assertIn("attempt=2/2", output)
        self.assertNotIn(self.user.email, output)

    def test_non_connection_failure_is_not_retried(self):
        from unittest.mock import patch

        with patch(
            "core.forms.EmailMultiAlternatives.send",
            side_effect=RuntimeError("unexpected"),
        ) as send:
            with self.assertLogs("core.forms", level="ERROR"):
                response = self.request_reset()

        self.assertRedirects(response, reverse("core:password_reset_done"))
        self.assertEqual(send.call_count, 1)


class PasswordHasherConfigurationTests(TestCase):
    def test_argon2_is_the_preferred_hasher(self):
        self.assertEqual(
            settings.PASSWORD_HASHERS[0],
            "django.contrib.auth.hashers.Argon2PasswordHasher",
        )

    def test_new_user_is_hashed_with_argon2(self):
        user = User.objects.create_user(email="argon@example.com", password=PASSWORD)
        self.assertTrue(user.password.startswith("argon2$"))

    def test_successful_login_upgrades_a_legacy_password_hash(self):
        legacy_hash = PBKDF2PasswordHasher().encode(PASSWORD, "legacysalt", iterations=1000)
        user = User.objects.create(email="legacy@example.com", password=legacy_hash)
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))

        response = self.client.post(
            reverse("core:login"),
            {"email": user.email, "password": PASSWORD},
        )

        self.assertRedirects(response, reverse("core:index"))
        user.refresh_from_db()
        self.assertTrue(user.password.startswith("argon2$"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
