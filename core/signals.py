from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from allauth.socialaccount.signals import (
    social_account_added,
    social_account_updated,
)

from .adapters import populate_google_identity
from .models import User
from .profile_images import import_google_profile_image


@receiver(pre_save, sender=User)
def capture_previous_account_status(sender, instance, **kwargs):
    """Remember the stored status so approval transitions can be detected."""
    if not instance.pk:
        instance._previous_account_status = None
        return
    instance._previous_account_status = (
        User.objects.filter(pk=instance.pk)
        .values_list("account_status", flat=True)
        .first()
    )


@receiver(post_save, sender=User)
def notify_membership_approval(sender, instance, created, **kwargs):
    """Create the approval notification only on PENDING -> APPROVED."""
    if created:
        return
    previous = getattr(instance, "_previous_account_status", None)
    if (
        previous == User.AccountStatus.PENDING
        and instance.account_status == User.AccountStatus.APPROVED
    ):
        from .notifications import handle_membership_approved

        try:
            handle_membership_approved(instance)
        except Exception:  # noqa: BLE001 - never break the approval save
            import logging

            logging.getLogger(__name__).exception(
                "Could not create membership approval notification for user id %s.",
                instance.pk,
            )


@receiver(user_logged_in)
def notify_user_login(sender, request, user, **kwargs):
    """Create one in-app notification for an actual login event."""
    if request is not None and request.session.pop(
        "suppress_login_notification", False
    ):
        return
    from .notifications import handle_user_login

    try:
        handle_user_login(user)
    except Exception:  # noqa: BLE001 - login must still succeed
        import logging

        logging.getLogger(__name__).exception(
            "Could not create login notification for user id %s.", user.pk
        )


@receiver(social_account_added)
@receiver(social_account_updated)
def sync_google_profile_image(sender, request, sociallogin, **kwargs):
    account = getattr(sociallogin, "account", None)
    if not account or account.provider != "google":
        return
    user = getattr(account, "user", None) or getattr(sociallogin, "user", None)
    populate_google_identity(user, account.extra_data)
    import_google_profile_image(user, account.extra_data.get("picture"))
