import io
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import User
from .profile_images import import_google_profile_image
from .signals import sync_google_profile_image


PASSWORD = "V7x!pQ2mR9#L"
GOOGLE_PICTURE_URL = "https://lh3.googleusercontent.com/avatar.png"


def image_bytes(image_format="PNG", size=(64, 64), color=(40, 100, 200)):
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    image.save(output, format=image_format)
    return output.getvalue()


def uploaded_image(name="avatar.png", image_format="PNG", size=(64, 64)):
    extension = image_format.lower()
    return SimpleUploadedFile(
        f"{name}.{extension}",
        image_bytes(image_format=image_format, size=size),
        content_type=f"image/{extension}",
    )


class FakeGoogleResponse:
    def __init__(self, content, content_type="image/png", status_code=200):
        self.content = content
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def iter_content(self, chunk_size=8192):
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index : index + chunk_size]

    def close(self):
        self.closed = True


class ProfileImageTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_dir,
            PRIVATE_MEDIA_ROOT=self.media_dir,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(shutil.rmtree, self.media_dir, True)

    def create_user(self, email="profile@example.com"):
        user = User.objects.create_user(
            email=email,
            password=PASSWORD,
            first_name="Mary",
            last_name="Watson",
        )
        self.client.force_login(user)
        return user

    def test_valid_upload_is_stored_and_rendered_app_wide(self):
        user = self.create_user()

        response = self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Watson",
                "email": "profile@example.com",
                "profile_image": uploaded_image(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        user.refresh_from_db()
        self.assertTrue(user.profile_image)
        expected_url = reverse("core:profile_image")
        expected_cached_url = user.effective_profile_image_url_versioned
        self.assertEqual(payload["profile_image_url"], expected_cached_url)
        self.assertEqual(user.effective_profile_image_url, expected_url)
        self.assertTrue(expected_cached_url.startswith(expected_url + "?v="))

        settings_page = self.client.get(reverse("core:settings"))
        self.assertContains(settings_page, expected_url)
        self.assertContains(
            settings_page, f'data-user-avatar-url="{expected_cached_url}"'
        )

    def test_replacing_manual_image_removes_previous_file(self):
        user = self.create_user()
        self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Watson",
                "email": "profile@example.com",
                "profile_image": uploaded_image("first"),
            },
        )
        user.refresh_from_db()
        first_name = user.profile_image.name
        first_version = user.effective_profile_image_version
        storage = user.profile_image.storage
        self.assertTrue(storage.exists(first_name))

        self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Watson",
                "email": "profile@example.com",
                "profile_image": uploaded_image("second"),
            },
        )

        user.refresh_from_db()
        self.assertNotEqual(user.profile_image.name, first_name)
        self.assertNotEqual(user.effective_profile_image_version, first_version)
        self.assertTrue(storage.exists(user.profile_image.name))
        self.assertFalse(storage.exists(first_name))

    def test_details_only_update_preserves_image_and_version(self):
        user = self.create_user()
        self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Watson",
                "email": "profile@example.com",
                "profile_image": uploaded_image("first"),
            },
        )
        user.refresh_from_db()
        image_name = user.profile_image.name
        image_version = user.effective_profile_image_version

        response = self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Jane Watson",
                "email": "profile@example.com",
            },
        )
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.get_full_name(), "Mary Jane Watson")
        self.assertEqual(user.profile_image.name, image_name)
        self.assertEqual(user.effective_profile_image_version, image_version)
        self.assertEqual(
            response.json()["profile_image_url"],
            user.effective_profile_image_url_versioned,
        )

    def test_image_only_update_preserves_details(self):
        user = self.create_user()
        response = self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Watson",
                "email": "profile@example.com",
                "profile_image": uploaded_image("first"),
            },
        )
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Mary")
        self.assertEqual(user.last_name, "Watson")
        self.assertEqual(user.email, "profile@example.com")
        self.assertTrue(user.profile_image)
        self.assertIn("?v=", response.json()["profile_image_url"])

    def test_manual_image_takes_precedence_over_google_image(self):
        user = self.create_user()
        user.google_profile_image.save(
            "google.png", ContentFile(image_bytes()), save=False
        )
        user.google_profile_image_source = GOOGLE_PICTURE_URL
        user.save(update_fields=["google_profile_image", "google_profile_image_source"])

        response = self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Watson",
                "email": "profile@example.com",
                "profile_image": uploaded_image(),
            },
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.profile_image)
        self.assertEqual(
            user.effective_profile_image.name, user.profile_image.name
        )
        self.assertFalse(user.uses_google_profile_image)

    def test_removing_manual_image_preserves_google_and_makes_it_effective(self):
        user = self.create_user()
        user.profile_image.save("manual.png", ContentFile(image_bytes()), save=False)
        user.google_profile_image.save(
            "google.png", ContentFile(image_bytes()), save=False
        )
        user.google_profile_image_source = GOOGLE_PICTURE_URL
        user.save(
            update_fields=[
                "profile_image",
                "google_profile_image",
                "google_profile_image_source",
            ]
        )
        manual_name = user.profile_image.name
        google_name = user.google_profile_image.name
        manual_storage = user.profile_image.storage
        google_storage = user.google_profile_image.storage

        response = self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Watson",
                "email": "profile@example.com",
                "remove_profile_image": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        expected_url = user.effective_profile_image_url_versioned
        self.assertEqual(response.json()["profile_image_url"], expected_url)
        self.assertFalse(user.profile_image)
        self.assertTrue(user.google_profile_image)
        self.assertTrue(user.google_profile_image_enabled)
        self.assertEqual(user.google_profile_image_source, GOOGLE_PICTURE_URL)
        self.assertEqual(user.google_profile_image.name, google_name)
        self.assertEqual(user.effective_profile_image.name, google_name)
        self.assertTrue(user.uses_google_profile_image)
        self.assertFalse(manual_storage.exists(manual_name))
        self.assertTrue(google_storage.exists(google_name))

        with patch("core.profile_images.requests.get") as requests_get:
            imported = import_google_profile_image(user, GOOGLE_PICTURE_URL)
        self.assertFalse(imported)
        requests_get.assert_not_called()

    def test_removing_manual_image_without_google_uses_default_avatar(self):
        user = self.create_user()
        user.profile_image.save("manual.png", ContentFile(image_bytes()), save=False)
        user.save(update_fields=["profile_image"])
        manual_name = user.profile_image.name
        storage = user.profile_image.storage

        response = self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Watson",
                "email": "profile@example.com",
                "remove_profile_image": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profile_image_url"], "")
        user.refresh_from_db()
        self.assertFalse(user.profile_image)
        self.assertIsNone(user.effective_profile_image)
        self.assertFalse(storage.exists(manual_name))
        settings_page = self.client.get(reverse("core:settings"))
        self.assertContains(
            settings_page,
            "assets/images/profiles/gabsweb-default-avatar.png",
        )
        self.assertNotContains(settings_page, "emma.jpg")

    def test_invalid_file_is_rejected(self):
        self.create_user()

        response = self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Watson",
                "email": "profile@example.com",
                "profile_image": SimpleUploadedFile(
                    "avatar.png", b"not an image", content_type="image/png"
                ),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("profile_image", response.json()["errors"])
        self.assertFalse(User.objects.get(email="profile@example.com").profile_image)

    def test_oversized_image_is_rejected(self):
        self.create_user()

        with patch("core.profile_images.MAX_UPLOAD_BYTES", 10):
            response = self.client.post(
                reverse("core:update_profile"),
                {
                    "name": "Mary Watson",
                    "email": "profile@example.com",
                    "profile_image": uploaded_image(),
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("profile_image", response.json()["errors"])

    def test_google_import_is_validated_stored_and_deduplicated(self):
        user = self.create_user()
        response = FakeGoogleResponse(image_bytes())

        with patch(
            "core.profile_images.requests.get", return_value=response
        ) as requests_get:
            imported = import_google_profile_image(user, GOOGLE_PICTURE_URL)

        self.assertTrue(imported)
        requests_get.assert_called_once()
        self.assertTrue(response.closed)
        user.refresh_from_db()
        self.assertTrue(user.google_profile_image)
        self.assertEqual(user.google_profile_image_source, GOOGLE_PICTURE_URL)
        self.assertEqual(
            user.effective_profile_image_url, reverse("core:profile_image")
        )

        with patch("core.profile_images.requests.get") as requests_get:
            imported_again = import_google_profile_image(user, GOOGLE_PICTURE_URL)
        self.assertFalse(imported_again)
        requests_get.assert_not_called()

    def test_google_import_rejects_untrusted_hosts_without_downloading(self):
        user = self.create_user()

        with patch("core.profile_images.requests.get") as requests_get:
            imported = import_google_profile_image(
                user, "https://example.com/avatar.png"
            )

        self.assertFalse(imported)
        requests_get.assert_not_called()

    def test_google_import_rejects_non_image_content(self):
        user = self.create_user()
        response = FakeGoogleResponse(b"not an image")

        with patch("core.profile_images.requests.get", return_value=response):
            with self.assertLogs("core.profile_images", level="WARNING"):
                imported = import_google_profile_image(user, GOOGLE_PICTURE_URL)

        self.assertFalse(imported)
        user.refresh_from_db()
        self.assertFalse(user.google_profile_image)

    def test_social_account_signal_imports_google_picture(self):
        user = self.create_user()
        sociallogin = SimpleNamespace(
            user=user,
            account=SimpleNamespace(
                provider="google",
                extra_data={"picture": GOOGLE_PICTURE_URL},
                user=user,
            ),
        )

        with patch(
            "core.profile_images.requests.get",
            return_value=FakeGoogleResponse(image_bytes()),
        ):
            sync_google_profile_image(
                sender=None, request=None, sociallogin=sociallogin
            )

        user.refresh_from_db()
        self.assertTrue(user.google_profile_image)
        self.assertEqual(
            user.effective_profile_image_url, reverse("core:profile_image")
        )

    def test_default_avatar_asset_and_references(self):
        asset = "assets/images/profiles/gabsweb-default-avatar.png"
        self.assertIsNotNone(finders.find(asset))
        self.assertNotIn(
            "emma.jpg",
            Path("static/assets/js/global-navigation.js").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "emma.jpg",
            Path("static/assets/js/settings.js").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "emma.jpg",
            Path("core/templates/partials/app_sidebar.html").read_text(
                encoding="utf-8"
            ),
        )

    def test_anonymous_user_cannot_retrieve_profile_image(self):
        user = self.create_user()
        user.profile_image.save(
            "manual.png",
            ContentFile(image_bytes(color=(11, 22, 33))),
            save=False,
        )
        user.save(update_fields=["profile_image"])
        self.client.logout()

        response = self.client.get(reverse("core:profile_image"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_owner_can_retrieve_profile_image_with_private_headers(self):
        user = self.create_user()
        expected = image_bytes(color=(11, 22, 33))
        user.profile_image.save("manual.png", ContentFile(expected), save=False)
        user.save(update_fields=["profile_image"])

        response = self.client.get(reverse("core:profile_image"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(b"".join(response.streaming_content), expected)
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Vary"], "Cookie")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_other_authenticated_user_cannot_retrieve_target_image(self):
        target = self.create_user("target@example.com")
        target.profile_image.save(
            "target.png",
            ContentFile(image_bytes(color=(99, 88, 77))),
            save=False,
        )
        target.save(update_fields=["profile_image"])
        other = User.objects.create_user(
            email="other@example.com", password=PASSWORD
        )
        self.client.force_login(other)

        response = self.client.get(reverse("core:profile_image"))

        self.assertEqual(response.status_code, 404)

    def test_guessed_user_specific_and_media_urls_are_not_retrievable(self):
        target = self.create_user("target@example.com")
        target.profile_image.save(
            "target.png",
            ContentFile(image_bytes(color=(99, 88, 77))),
            save=False,
        )
        target.save(update_fields=["profile_image"])
        other = User.objects.create_user(
            email="other@example.com", password=PASSWORD
        )
        self.client.force_login(other)

        guessed = self.client.get(f"/profile/image/{target.pk}/")
        leaked_media = self.client.get(f"/media/{target.profile_image.name}")

        self.assertEqual(guessed.status_code, 404)
        self.assertNotEqual(leaked_media.status_code, 200)
        self.assertNotEqual(leaked_media.content, image_bytes(color=(99, 88, 77)))


class ProfileIdentityAndConsistencyTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_dir,
            PRIVATE_MEDIA_ROOT=self.media_dir,
            PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(shutil.rmtree, self.media_dir, True)

    def test_normal_registration_without_image_uses_default_avatar(self):
        response = self.client.post(
            reverse("core:register"),
            {
                "full_name": "Mary Jane Watson",
                "email": "mary@example.com",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "workplace_access": "no",
                "terms": "on",
            },
        )
        self.assertRedirects(response, reverse("core:index"))

        user = User.objects.get(email="mary@example.com")
        self.assertEqual(user.first_name, "Mary")
        self.assertEqual(user.last_name, "Jane Watson")
        self.assertFalse(user.profile_image)
        self.assertFalse(user.google_profile_image)

        page = self.client.get(reverse("core:settings"))
        self.assertContains(page, 'data-user-full-name="Mary Jane Watson"')
        self.assertContains(page, 'data-user-email="mary@example.com"')
        self.assertContains(page, 'data-user-avatar-url=""')
        self.assertContains(
            page,
            'assets/images/profiles/gabsweb-default-avatar.png',
        )

    def test_invalid_upload_preserves_existing_manual_image(self):
        user = User.objects.create_user(
            email="profile@example.com",
            password=PASSWORD,
            first_name="Mary",
            last_name="Watson",
        )
        self.client.force_login(user)
        self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Watson",
                "email": "profile@example.com",
                "profile_image": uploaded_image("first"),
            },
        )
        user.refresh_from_db()
        original_name = user.profile_image.name
        original_version = user.effective_profile_image_version
        storage = user.profile_image.storage

        response = self.client.post(
            reverse("core:update_profile"),
            {
                "name": "Mary Watson",
                "email": "profile@example.com",
                "profile_image": SimpleUploadedFile(
                    "broken.png", b"not an image", content_type="image/png"
                ),
            },
        )

        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertEqual(user.profile_image.name, original_name)
        self.assertEqual(user.effective_profile_image_version, original_version)
        self.assertTrue(storage.exists(original_name))
        self.assertEqual(user.effective_profile_image.name, original_name)

    def test_google_import_keeps_manual_image_effective(self):
        user = User.objects.create_user(
            email="profile@example.com",
            password=PASSWORD,
            first_name="Mary",
            last_name="Watson",
        )
        user.profile_image.save("manual.png", ContentFile(image_bytes()), save=False)
        user.save(update_fields=["profile_image"])
        manual_name = user.profile_image.name

        with patch(
            "core.profile_images.requests.get",
            return_value=FakeGoogleResponse(image_bytes(color=(9, 8, 7))),
        ):
            imported = import_google_profile_image(user, GOOGLE_PICTURE_URL)

        self.assertTrue(imported)
        user.refresh_from_db()
        self.assertEqual(user.profile_image.name, manual_name)
        self.assertEqual(user.effective_profile_image.name, manual_name)
        self.assertFalse(user.uses_google_profile_image)
        self.assertTrue(user.google_profile_image)

    def test_effective_image_is_consistent_on_authenticated_pages(self):
        user = User.objects.create_user(
            email="profile@example.com",
            password=PASSWORD,
            first_name="Mary",
            last_name="Watson",
        )
        user.profile_image.save("manual.png", ContentFile(image_bytes()), save=False)
        user.save(update_fields=["profile_image"])
        self.client.force_login(user)
        expected_url = user.effective_profile_image_url_versioned
        self.assertIn("?v=", expected_url)

        for route_name in (
            "core:index",
            "core:transaction",
            "core:budgets",
            "core:savings",
            "core:goals",
            "core:insights",
            "core:planner",
            "core:work_schedule",
            "core:settings",
        ):
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response, f'data-user-avatar-url="{expected_url}"'
                )
                self.assertContains(
                    response,
                    f'src="{expected_url}"',
                )
