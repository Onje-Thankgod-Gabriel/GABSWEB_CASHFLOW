from django.conf import settings

from .models import User


def workplace_access(request):
    user = getattr(request, "user", None)
    approved = bool(
        user
        and user.is_authenticated
        and user.account_status == User.AccountStatus.APPROVED
    )
    return {
        "can_access_workplace_schedule": approved,
        "google_oauth_configured": bool(
            getattr(settings, "GOOGLE_OAUTH_CONFIGURED", False)
        ),
    }
