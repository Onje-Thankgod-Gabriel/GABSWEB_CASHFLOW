from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.urls import reverse

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from .profile_images import import_google_profile_image


def populate_google_identity(user, extra_data):
    """Fill only missing name fields from a verified Google profile."""
    if not user or not isinstance(extra_data, dict):
        return
    first_name = (extra_data.get("given_name") or "").strip()
    last_name = (extra_data.get("family_name") or "").strip()
    if not first_name or not last_name:
        full_name = (extra_data.get("name") or "").strip()
        if full_name:
            parts = full_name.split(maxsplit=1)
            if not first_name:
                first_name = parts[0]
            if not last_name and len(parts) > 1:
                last_name = parts[1]

    updates = []
    if first_name and not (user.first_name or "").strip():
        user.first_name = first_name
        updates.append("first_name")
    if last_name and not (user.last_name or "").strip():
        user.last_name = last_name
        updates.append("last_name")
    if updates:
        user.save(update_fields=updates)


class GabswebAccountAdapter(DefaultAccountAdapter):
    """Keep allauth's local signup closed; Gabsweb has its own registration."""

    def is_open_for_signup(self, request):
        return False

    def respond_user_inactive(self, request, user):
        messages.error(
            request,
            "This account is inactive. Contact the platform owner if you need access.",
        )
        return redirect("core:login")


class GabswebSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return True

    def pre_social_login(self, request, sociallogin):
        verified_emails = [
            address.email
            for address in sociallogin.email_addresses
            if address.email and address.verified
        ]
        if not verified_emails:
            messages.error(
                request,
                "Google did not provide a verified email address, so sign-in was cancelled.",
            )
            raise ImmediateHttpResponse(redirect("core:login"))

    def get_connect_redirect_url(self, request, socialaccount):
        if request.user.is_authenticated:
            return reverse("core:settings")
        return reverse("core:login")

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        if form is None:
            user.account_status = get_user_model().AccountStatus.GENERAL
            user.is_staff = False
            user.is_superuser = False
            user.save(update_fields=["account_status", "is_staff", "is_superuser"])
        extra_data = sociallogin.account.extra_data
        populate_google_identity(user, extra_data)
        import_google_profile_image(user, extra_data.get("picture"))
        if request is not None:
            request.session["suppress_login_notification"] = True
            from .notifications import handle_new_registration

            try:
                handle_new_registration(user)
            except Exception:  # noqa: BLE001 - signup must still succeed
                import logging

                logging.getLogger(__name__).exception(
                    "Could not create welcome notification for user id %s.",
                    user.pk,
                )
        return user
