from django.conf import settings

from .models import User


def workplace_access(request):
    user = getattr(request, "user", None)
    approved = bool(
        user
        and user.is_authenticated
        and user.account_status == User.AccountStatus.APPROVED
    )
    pwa_login_prompt = False
    if user and user.is_authenticated and hasattr(request, "session"):
        pwa_login_prompt = bool(request.session.get("gabsweb_pwa_prompt", False))
        if pwa_login_prompt:
            request._gabsweb_pwa_prompt_rendered = True
    pending_notice_full = False
    if user and user.is_authenticated and hasattr(request, "session"):
        pending_notice_full = bool(
            request.session.pop("gabsweb_pending_notice_full", False)
        )
    return {
        "can_access_workplace_schedule": approved,
        "pwa_login_prompt": pwa_login_prompt,
        "pending_notice_full": pending_notice_full,
        "site_base_url": getattr(
            settings,
            "SITE_BASE_URL",
            "https://cashflow.gabsweb.com.ng",
        ).rstrip("/"),
        "google_oauth_configured": bool(
            getattr(settings, "GOOGLE_OAUTH_CONFIGURED", False)
        ),
    }
