import io
import tempfile
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core import mail
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from allauth.account.models import EmailAddress
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialAccount, SocialLogin
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from PIL import Image

from .models import User

from unittest.mock import patch


PASSWORD = "V7x!pQ2mR9#L"
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


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SOCIALACCOUNT_PROVIDERS=GOOGLE_PROVIDERS,
    GOOGLE_OAUTH_CONFIGURED=True,
)
class GoogleOAuthTests(TestCase):
    def build_sociallogin(
        self,
        email="google@example.com",
        uid="google-123",
        verified=True,
        first_name="Ada",
        last_name="Lovelace",
        picture="https://example.com/avatar.jpg",
    ):
        provider = get_adapter().get_provider(None, "google")
        account = SocialAccount(
            provider="google",
            uid=uid,
            extra_data={
                "email": email,
                "email_verified": verified,
                "given_name": first_name,
                "family_name": last_name,
                "name": f"{first_name} {last_name}",
                "picture": picture,
            },
        )
        address = EmailAddress(email=email, verified=verified, primary=True)
        login = SocialLogin(
            provider=provider,
            account=account,
            email_addresses=[address],
        )
        login.user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
        )
        return login

    def start_flow(self, process="login"):
        login_url = f"{reverse('google_login')}?process={process}"
        confirmation = self.client.get(login_url)
        self.assertEqual(confirmation.status_code, 200)
        response = self.client.post(login_url)
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertIn("accounts.google.com", location)
        state = parse_qs(urlparse(location).query)["state"][0]
        return state

    def complete_callback(self, state, sociallogin, token_error=None):
        if token_error:
            patcher = patch.object(
                GoogleOAuth2Adapter,
                "get_access_token_data",
                side_effect=token_error,
            )
        else:
            patcher = patch.object(
                GoogleOAuth2Adapter,
                "get_access_token_data",
                return_value={"access_token": "fake-access-token"},
            )
        with patcher, patch.object(
            GoogleOAuth2Adapter, "complete_login", return_value=sociallogin
        ):
            return self.client.get(
                reverse("google_callback"),
                {"state": state, "code": "fake-code"},
            )

    def sign_up_social_user(self, workplace_access="no", extra=None, picture=None):
        state = self.start_flow()
        sociallogin = self.build_sociallogin(**({"picture": picture} if picture else {}))
        response = self.complete_callback(state, sociallogin)
        self.assertRedirects(response, reverse("socialaccount_signup"))

        payload = {
            "email": sociallogin.user.email,
            "workplace_access": workplace_access,
            "terms": "on",
        }
        if extra:
            payload.update(extra)
        response = self.client.post(reverse("socialaccount_signup"), payload)
        return response, sociallogin

    def test_google_picture_is_imported_on_signup_and_rendered(self):
        image = Image.new("RGB", (48, 48), (20, 120, 80))
        output = io.BytesIO()
        image.save(output, format="PNG")
        content = output.getvalue()

        class FakeResponse:
            status_code = 200
            headers = {"Content-Type": "image/png"}

            def iter_content(self, chunk_size=8192):
                yield content

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root,
            PRIVATE_MEDIA_ROOT=media_root,
        ), patch("core.profile_images.requests.get", return_value=FakeResponse()):
            response, sociallogin = self.sign_up_social_user(
                picture="https://lh3.googleusercontent.com/avatar.png"
            )

            self.assertRedirects(response, reverse("core:index"))
            user = User.objects.get(email=sociallogin.user.email)
            self.assertTrue(user.google_profile_image)
            self.assertEqual(
                user.effective_profile_image_url, reverse("core:profile_image")
            )
            settings_page = self.client.get(reverse("core:settings"))
            self.assertContains(settings_page, reverse("core:profile_image"))

    def test_google_picture_download_failure_does_not_block_signup(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root,
            PRIVATE_MEDIA_ROOT=media_root,
        ), patch(
            "core.profile_images.requests.get",
            side_effect=RuntimeError("network unavailable"),
        ):
            with self.assertLogs("core.profile_images", level="WARNING"):
                response, sociallogin = self.sign_up_social_user(
                    picture="https://lh3.googleusercontent.com/avatar.png"
                )

        self.assertRedirects(response, reverse("core:index"))
        user = User.objects.get(email=sociallogin.user.email)
        self.assertFalse(user.google_profile_image)
        self.assertEqual(user.effective_profile_image_url, "")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertEqual(user.account_status, User.AccountStatus.GENERAL)

    def test_returning_google_user_preserves_manual_image_and_identity(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root,
            PRIVATE_MEDIA_ROOT=media_root,
        ), patch(
            "core.profile_images.requests.get",
            side_effect=RuntimeError("network unavailable"),
        ):
            user = User.objects.create_user(
                email="returning@example.com",
                password=PASSWORD,
                first_name="Edited",
                last_name="Person",
                account_status=User.AccountStatus.PENDING,
            )
            image = Image.new("RGB", (32, 32), (1, 2, 3))
            output = io.BytesIO()
            image.save(output, format="PNG")
            user.profile_image.save(
                "manual.png", ContentFile(output.getvalue()), save=True
            )
            manual_name = user.profile_image.name
            SocialAccount.objects.create(
                user=user,
                provider="google",
                uid="google-returning",
                extra_data={"email": user.email},
            )

            state = self.start_flow()
            response = self.complete_callback(
                state,
                self.build_sociallogin(
                    email=user.email,
                    uid="google-returning",
                    first_name="Google",
                    last_name="Name",
                ),
            )

        self.assertRedirects(response, reverse("core:index"))
        user.refresh_from_db()
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertEqual(user.account_status, User.AccountStatus.PENDING)
        self.assertEqual(user.first_name, "Edited")
        self.assertEqual(user.last_name, "Person")
        self.assertEqual(user.profile_image.name, manual_name)
        self.assertEqual(user.effective_profile_image.name, manual_name)
        self.assertFalse(user.uses_google_profile_image)

    def test_successful_callback_creates_new_google_user(self):
        response, sociallogin = self.sign_up_social_user()

        self.assertRedirects(response, reverse("core:index"))
        user = User.objects.get(email=sociallogin.user.email)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.first_name, "Ada")
        self.assertEqual(user.last_name, "Lovelace")
        self.assertEqual(user.account_status, User.AccountStatus.GENERAL)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        account = SocialAccount.objects.get(user=user, provider="google")
        self.assertEqual(account.uid, "google-123")
        self.assertTrue(account.extra_data["email_verified"])

    def test_google_signup_sets_single_use_pwa_install_prompt(self):
        response, _sociallogin = self.sign_up_social_user()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:index"))
        self.assertTrue(self.client.session.get("gabsweb_pwa_prompt"))

        first_render = self.client.get(reverse("core:index"))
        self.assertContains(first_render, 'data-pwa-login-prompt="true"')
        self.assertNotIn("gabsweb_pwa_prompt", self.client.session)

        refresh = self.client.get(reverse("core:index"))
        self.assertContains(refresh, 'data-pwa-login-prompt="false"')

    def test_returning_google_login_sets_single_use_pwa_install_prompt(self):
        user = User.objects.create_user(
            email="google-return@example.com",
            password=PASSWORD,
        )
        SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="google-return",
            extra_data={"email": user.email},
        )
        state = self.start_flow()
        response = self.complete_callback(
            state,
            self.build_sociallogin(
                email=user.email,
                uid="google-return",
                first_name="Return",
                last_name="User",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:index"))
        self.assertTrue(self.client.session.get("gabsweb_pwa_prompt"))

        first_render = self.client.get(reverse("core:index"))
        self.assertContains(first_render, 'data-pwa-login-prompt="true"')
        self.assertNotIn("gabsweb_pwa_prompt", self.client.session)

    def test_missing_state_is_rejected(self):
        response = self.client.get(reverse("google_callback"), {"code": "fake-code"})

        self.assertEqual(response.status_code, 401)
        self.assertContains(response, "Google Sign In Failed", status_code=401)
        self.assertFalse(User.objects.exists())

    def test_invalid_state_is_rejected(self):
        response = self.client.get(
            reverse("google_callback"),
            {"state": "not-a-real-state", "code": "fake-code"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertFalse(User.objects.exists())

    def test_cancelled_authorization_shows_cancelled_page(self):
        state = self.start_flow()
        response = self.client.get(
            reverse("google_callback"),
            {"state": state, "error": "access_denied"},
        )

        self.assertRedirects(response, reverse("socialaccount_login_cancelled"))
        followed = self.client.get(reverse("socialaccount_login_cancelled"))
        self.assertContains(followed, "Google Sign In Cancelled")
        self.assertFalse(User.objects.exists())

    def test_unverified_google_email_is_rejected(self):
        state = self.start_flow()
        sociallogin = self.build_sociallogin(verified=False)
        response = self.complete_callback(state, sociallogin)

        self.assertRedirects(response, reverse("core:login"))
        self.assertFalse(User.objects.exists())

    def test_failed_callback_shows_error_page(self):
        state = self.start_flow()
        response = self.complete_callback(
            state,
            self.build_sociallogin(),
            token_error=OAuth2Error("token exchange failed"),
        )

        self.assertEqual(response.status_code, 401)
        self.assertContains(response, "Google Sign In Failed", status_code=401)
        self.assertFalse(User.objects.exists())

    def test_duplicate_email_requires_password_sign_in_and_explicit_link(self):
        existing = User.objects.create_user(
            email="google@example.com",
            password=PASSWORD,
        )
        state = self.start_flow()
        sociallogin = self.build_sociallogin()
        response = self.complete_callback(state, sociallogin)
        self.assertRedirects(response, reverse("socialaccount_signup"))

        response = self.client.post(
            reverse("socialaccount_signup"),
            {
                "email": "google@example.com",
                "workplace_access": "no",
                "terms": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account already exists with this email address")
        self.assertFalse(SocialAccount.objects.filter(user=existing).exists())
        self.assertEqual(User.objects.filter(email="google@example.com").count(), 1)
        self.assertFalse(User.objects.filter(email="second@example.com").exists())

    def test_logged_in_user_can_explicitly_link_google(self):
        user = User.objects.create_user(
            email="linked@example.com",
            password=PASSWORD,
            account_status=User.AccountStatus.PENDING,
        )
        self.client.force_login(user)

        response = self.client.post(reverse("core:google_connect"))
        self.assertRedirects(
            response,
            f"{reverse('google_login')}?process=connect",
            fetch_redirect_response=False,
        )
        state = self.start_flow(process="connect")
        response = self.complete_callback(
            state,
            self.build_sociallogin(email="linked@example.com", uid="google-linked"),
        )

        self.assertRedirects(response, reverse("core:settings"))
        user.refresh_from_db()
        self.assertEqual(user.account_status, User.AccountStatus.PENDING)
        self.assertTrue(user.has_usable_password())
        self.assertTrue(check_password(PASSWORD, user.password))
        self.assertEqual(
            SocialAccount.objects.filter(user=user, provider="google").count(), 1
        )
        settings_page = self.client.get(reverse("core:settings"))
        self.assertContains(settings_page, "Connected")
        self.assertContains(settings_page, "Disconnect Google")

    def test_cancelled_connect_leaves_the_existing_account_unchanged(self):
        user = User.objects.create_user(
            email="cancelled-link@example.com",
            password=PASSWORD,
            account_status=User.AccountStatus.APPROVED,
        )
        self.client.force_login(user)

        self.client.post(reverse("core:google_connect"))
        state = self.start_flow(process="connect")
        response = self.client.get(
            reverse("google_callback"),
            {"state": state, "error": "access_denied"},
        )

        self.assertRedirects(response, reverse("socialaccount_login_cancelled"))
        self.assertFalse(
            SocialAccount.objects.filter(user=user, provider="google").exists()
        )
        self.assertTrue(user.has_usable_password())
        user.refresh_from_db()
        self.assertEqual(user.account_status, User.AccountStatus.APPROVED)

    def test_repeated_connect_attempt_reports_the_existing_connection(self):
        user = User.objects.create_user(
            email="repeat-link@example.com", password=PASSWORD
        )
        SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="google-repeat",
            extra_data={"email": user.email},
        )
        self.client.force_login(user)

        response = self.client.post(reverse("core:google_connect"), follow=True)

        self.assertRedirects(response, reverse("core:settings"))
        self.assertContains(
            response, "A Google account is already connected to your profile."
        )
        self.assertEqual(
            SocialAccount.objects.filter(user=user, provider="google").count(), 1
        )

    def test_linking_preserves_approved_status(self):
        user = User.objects.create_user(
            email="approved@example.com",
            password=PASSWORD,
            account_status=User.AccountStatus.APPROVED,
        )
        self.client.force_login(user)

        self.client.post(reverse("core:google_connect"))
        state = self.start_flow(process="connect")
        response = self.complete_callback(
            state,
            self.build_sociallogin(email="approved@example.com", uid="google-approved"),
        )

        self.assertRedirects(response, reverse("core:settings"))
        user.refresh_from_db()
        self.assertEqual(user.account_status, User.AccountStatus.APPROVED)

    def test_inactive_linked_user_cannot_sign_in_with_google(self):
        user = User.objects.create_user(
            email="inactive@example.com",
            password=PASSWORD,
            is_active=False,
        )
        SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="google-inactive",
            extra_data={"email": user.email},
        )
        state = self.start_flow()
        response = self.complete_callback(
            state,
            self.build_sociallogin(email=user.email, uid="google-inactive"),
        )

        self.assertRedirects(response, reverse("core:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_anonymous_user_cannot_link_google(self):
        response = self.client.post(reverse("core:google_connect"))

        self.assertRedirects(
            response,
            f"{reverse('core:login')}?next={reverse('core:google_connect')}",
        )
        self.assertFalse(SocialAccount.objects.exists())

    def test_connect_callback_without_session_does_not_link(self):
        state = self.start_flow(process="connect")
        response = self.complete_callback(
            state,
            self.build_sociallogin(email="anonymous@example.com", uid="google-anon"),
        )

        self.assertRedirects(
            response,
            reverse("core:login"),
            fetch_redirect_response=False,
        )
        self.assertFalse(SocialAccount.objects.exists())

    def test_google_identity_cannot_be_linked_to_two_local_accounts(self):
        owner = User.objects.create_user(
            email="owner@example.com",
            password=PASSWORD,
        )
        SocialAccount.objects.create(
            user=owner,
            provider="google",
            uid="google-used",
            extra_data={"email": "owner@example.com"},
        )
        other = User.objects.create_user(
            email="other@example.com",
            password=PASSWORD,
        )
        self.client.force_login(other)

        self.client.post(reverse("core:google_connect"))
        state = self.start_flow(process="connect")
        response = self.complete_callback(
            state,
            self.build_sociallogin(email="other@example.com", uid="google-used"),
        )

        self.assertRedirects(response, reverse("core:settings"))
        self.assertEqual(SocialAccount.objects.filter(provider="google").count(), 1)
        self.assertEqual(
            SocialAccount.objects.get(provider="google", uid="google-used").user,
            owner,
        )

    def test_google_connect_requires_post_and_csrf(self):
        user = User.objects.create_user(email="csrf@example.com", password=PASSWORD)
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)

        response = client.get(reverse("core:google_connect"))
        self.assertEqual(response.status_code, 405)

        response = client.post(reverse("core:google_connect"))
        self.assertEqual(response.status_code, 403)

    def test_workplace_no_creates_general_account(self):
        response, sociallogin = self.sign_up_social_user(workplace_access="no")

        self.assertRedirects(response, reverse("core:index"))
        user = User.objects.get(email=sociallogin.user.email)
        self.assertEqual(user.account_status, User.AccountStatus.GENERAL)

    def test_workplace_yes_creates_pending_account(self):
        response, sociallogin = self.sign_up_social_user(workplace_access="yes")

        self.assertRedirects(response, reverse("core:index"))
        user = User.objects.get(email=sociallogin.user.email)
        self.assertEqual(user.account_status, User.AccountStatus.PENDING)

    def test_google_signup_cannot_grant_approved_status_or_privileges(self):
        response, sociallogin = self.sign_up_social_user(
            workplace_access="yes",
            extra={
                "account_status": "APPROVED",
                "is_staff": "on",
                "is_superuser": "on",
            },
        )

        self.assertRedirects(response, reverse("core:index"))
        user = User.objects.get(email=sociallogin.user.email)
        self.assertEqual(user.account_status, User.AccountStatus.PENDING)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_linked_user_can_still_use_password_login(self):
        user = User.objects.create_user(
            email="both@example.com",
            password=PASSWORD,
        )
        SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="google-both",
            extra_data={"email": user.email},
        )

        response = self.client.post(
            reverse("core:login"),
            {"email": user.email, "password": PASSWORD},
        )

        self.assertRedirects(response, reverse("core:index"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    @override_settings(
        MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
    )
    def test_existing_password_reset_still_works(self):
        user = User.objects.create_user(
            email="reset-oauth@example.com",
            password=PASSWORD,
        )
        response = self.client.post(
            reverse("core:password_reset"),
            {"email": user.email},
        )

        self.assertRedirects(response, reverse("core:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])

    def test_google_signup_without_picture_uses_default_avatar_and_identity(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root,
            PRIVATE_MEDIA_ROOT=media_root,
        ), patch("core.profile_images.requests.get") as requests_get:
            response, sociallogin = self.sign_up_social_user(
                picture="https://example.com/no-picture.png"
            )

        requests_get.assert_not_called()
        self.assertRedirects(response, reverse("core:index"))
        user = User.objects.get(email=sociallogin.user.email)
        self.assertEqual(user.first_name, "Ada")
        self.assertEqual(user.last_name, "Lovelace")
        self.assertEqual(user.email, "google@example.com")
        self.assertFalse(user.google_profile_image)
        self.assertEqual(user.effective_profile_image_url, "")

        settings_page = self.client.get(reverse("core:settings"))
        self.assertContains(settings_page, 'data-user-full-name="Ada Lovelace"')
        self.assertContains(settings_page, 'data-user-email="google@example.com"')
        self.assertContains(settings_page, 'data-user-avatar-url=""')
        self.assertContains(
            settings_page, "assets/images/profiles/gabsweb-default-avatar.png"
        )

    def test_linking_google_preserves_edited_identity_on_later_login(self):
        user = User.objects.create_user(
            email="local-editor@example.com",
            password=PASSWORD,
            first_name="Local",
            last_name="Editor",
        )
        self.client.force_login(user)
        self.client.post(reverse("core:google_connect"))
        state = self.start_flow(process="connect")
        response = self.complete_callback(
            state,
            self.build_sociallogin(
                email=user.email,
                uid="google-local-editor",
                first_name="Google",
                last_name="Person",
            ),
        )
        self.assertRedirects(response, reverse("core:settings"))
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Local")
        self.assertEqual(user.last_name, "Editor")

        self.client.logout()
        state = self.start_flow()
        response = self.complete_callback(
            state,
            self.build_sociallogin(
                email=user.email,
                uid="google-local-editor",
                first_name="Different",
                last_name="Name",
            ),
        )
        self.assertRedirects(response, reverse("core:index"))
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Local")
        self.assertEqual(user.last_name, "Editor")

        settings_page = self.client.get(reverse("core:settings"))
        self.assertContains(settings_page, 'data-user-full-name="Local Editor"')
        self.assertContains(settings_page, 'data-user-email="local-editor@example.com"')

    def test_linking_google_fills_only_missing_identity_fields(self):
        user = User.objects.create_user(
            email="blank-name@example.com",
            password=PASSWORD,
        )
        self.client.force_login(user)
        self.client.post(reverse("core:google_connect"))
        state = self.start_flow(process="connect")
        response = self.complete_callback(
            state,
            self.build_sociallogin(
                email=user.email,
                uid="google-blank-name",
                first_name="Ada",
                last_name="Lovelace",
            ),
        )

        self.assertRedirects(response, reverse("core:settings"))
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Ada")
        self.assertEqual(user.last_name, "Lovelace")

    def test_login_and_registration_pages_expose_google_flow(self):
        for route_name in ("core:login", "core:register"):
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Continue with Google")
            self.assertContains(response, reverse("google_login"))
            self.assertNotContains(
                response,
                "Google sign-in will be available with the authentication service.",
            )


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SOCIALACCOUNT_PROVIDERS={},
    GOOGLE_OAUTH_CONFIGURED=False,
)
class GoogleOAuthDisabledTests(TestCase):
    def test_login_page_keeps_google_control_disabled_without_credentials(self):
        response = self.client.get(reverse("core:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Google sign-in unavailable")
        self.assertNotContains(response, reverse("google_login"))

    def test_direct_google_login_redirects_without_credentials(self):
        response = self.client.get(
            reverse("google_login"),
            {"process": "login"},
            follow=True,
        )

        self.assertRedirects(response, reverse("core:login"))
        self.assertContains(response, "Google sign-in is not configured")

    def test_direct_google_callback_redirects_without_credentials(self):
        response = self.client.get(
            reverse("google_callback"),
            {"state": "unused", "code": "unused"},
            follow=True,
        )

        self.assertRedirects(response, reverse("core:login"))
        self.assertContains(response, "Google sign-in is not configured")
        self.assertFalse(User.objects.exists())
