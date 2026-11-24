"""Reusable in-app notification and email delivery helpers.

Notifications are always owned by the authenticated user who receives them.
Email delivery happens only after the database transaction that created the
notification has committed, so a temporary provider failure can never roll
back or hide the in-app notification.
"""

import logging
import smtplib
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .models import Notification, User

logger = logging.getLogger(__name__)

MEMBERSHIP_APPROVED_TITLE = "Application Approved"
WELCOME_TITLE = "Welcome to Gabsweb CashFlow"
WELCOME_MESSAGE = (
    "Your account has been created successfully. Welcome! You can now start "
    "setting up your personal financial dashboard."
)
LOGIN_TITLE = "New Login"
LOGIN_MESSAGE = "You have successfully signed in to your Gabsweb CashFlow account."
MAX_EMAIL_ATTEMPTS = 5

EMAIL_TEMPLATES = {
    Notification.EventType.MEMBERSHIP_APPROVED: (
        "templates/emails/membership_approved_subject.txt",
        "templates/emails/membership_approved_email.txt",
        "templates/emails/membership_approved_email.html",
    ),
    Notification.EventType.WELCOME: (
        "templates/emails/welcome_subject.txt",
        "templates/emails/welcome_email.txt",
        "templates/emails/welcome_email.html",
    ),
    Notification.EventType.LOGIN: (
        "templates/emails/login_subject.txt",
        "templates/emails/login_email.txt",
        "templates/emails/login_email.html",
    ),
}


def _public_base_url():
    base = (getattr(settings, "PASSWORD_RESET_BASE_URL", "") or "").strip()
    if base:
        return base.rstrip("/")
    if getattr(settings, "DEBUG", False):
        return "http://localhost:8000"
    return ""


def membership_label_for(user):
    if user is not None and getattr(user, "workplace_company", ""):
        return user.get_workplace_company_display()
    return "GCTN/Apex Pay"


def email_logo_url():
    url = getattr(settings, "EMAIL_LOGO_URL", "")
    if url and "/assets/icons/" in urlsplit(url).path:
        # Transactional email must show the brand mark, never an installed PWA
        # launcher icon. Keep the configured origin but use the brand asset.
        parts = urlsplit(url)
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                "/static/assets/images/gabsweb-cashflow-logo.png",
                parts.query,
                "",
            )
        )
    return url


def documentation_url():
    """Return the public explanation page URL when its named route exists.

    The welcome email must never contain a broken help link. The documentation
    page is being delivered separately, so the link is rendered only after the
    configured named route resolves.
    """
    name = getattr(settings, "DOCUMENTATION_URL_NAME", "")
    if not name:
        return ""
    try:
        path = reverse(name)
    except NoReverseMatch:
        return ""
    base = _public_base_url()
    return f"{base}{path}" if base else path


def email_context(user):
    base = _public_base_url()
    login_path = reverse("core:login")
    return {
        "user": user,
        "first_name": user.get_first_display_name(),
        "membership_label": membership_label_for(user),
        "login_url": f"{base}{login_path}" if base else login_path,
        "documentation_url": documentation_url(),
        "email_logo_url": email_logo_url(),
    }


def membership_approval_context(user):
    return email_context(user)


def create_notification(
    owner,
    *,
    title,
    message,
    event_type=Notification.EventType.ACCOUNT,
    dedupe_key=None,
    send_email=False,
):
    """Create one notification for ``owner`` and optionally queue its email.

    ``dedupe_key`` makes the operation idempotent per owner: repeated saves or
    requests return the existing record and never create or send a duplicate.
    """
    defaults = {
        "title": title,
        "message": message,
        "event_type": event_type,
        "email_status": (
            Notification.EmailStatus.PENDING
            if send_email
            else Notification.EmailStatus.NOT_REQUIRED
        ),
    }
    if dedupe_key:
        notification, created = Notification.objects.get_or_create(
            owner=owner, dedupe_key=dedupe_key, defaults=defaults
        )
    else:
        notification = Notification.objects.create(owner=owner, **defaults)
        created = True
    if created and send_email:
        notification_id = notification.pk
        transaction.on_commit(
            lambda: send_notification_email(notification_id)
        )
    return notification, created


def handle_membership_approved(user):
    """Store the approval notification once and queue its email once."""
    if user.account_status != User.AccountStatus.APPROVED:
        return None, False
    label = membership_label_for(user)
    return create_notification(
        user,
        title=MEMBERSHIP_APPROVED_TITLE,
        message=(
            f"Your {label} membership has been approved. You can "
            "now access the available workplace features."
        ),
        event_type=Notification.EventType.MEMBERSHIP_APPROVED,
        dedupe_key=f"membership-approved:{user.pk}",
        send_email=True,
    )


def handle_new_registration(user):
    """Create the one-time welcome notification and queue its email."""
    return create_notification(
        user,
        title=WELCOME_TITLE,
        message=WELCOME_MESSAGE,
        event_type=Notification.EventType.WELCOME,
        dedupe_key=f"welcome:{user.pk}",
        send_email=True,
    )


def handle_user_login(user):
    """Create a notification for this actual successful login event."""
    return create_notification(
        user,
        title=LOGIN_TITLE,
        message=LOGIN_MESSAGE,
        event_type=Notification.EventType.LOGIN,
        send_email=True,
    )


def _safe_error(exc):
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = sorted({code for code, _ in exc.recipients.values()})
        return f"recipient refused smtp_codes={codes}"
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"smtp_code={exc.smtp_code} response={str(exc.smtp_error)[:120]}"
    return f"{type(exc).__name__}: {str(exc)[:180]}"


def send_notification_email(notification_id):
    """Send one notification email, recording the result safely.

    This function never raises to its caller. A failure is logged without
    credentials or full recipient addresses and the notification is kept.
    """
    try:
        notification = Notification.objects.select_related("owner").get(
            pk=notification_id
        )
    except Notification.DoesNotExist:
        return False

    if notification.email_status == Notification.EmailStatus.SENT:
        return True
    if not notification.owner.email:
        return False
    if notification.email_attempts >= MAX_EMAIL_ATTEMPTS:
        return False

    templates = EMAIL_TEMPLATES.get(notification.event_type)
    if not templates:
        return False
    subject_template, text_template, html_template = templates
    context = email_context(notification.owner)
    try:
        subject = "".join(
            render_to_string(subject_template, context).splitlines()
        )
        body = render_to_string(text_template, context)
        html = render_to_string(html_template, context)
        message = EmailMultiAlternatives(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [notification.owner.email],
        )
        message.attach_alternative(html, "text/html")
        sent = message.send(fail_silently=False)
        if sent != 1:
            raise RuntimeError(f"email backend reported {sent} sent messages")
    except Exception as exc:  # noqa: BLE001 - delivery must not break approval
        notification.email_status = Notification.EmailStatus.FAILED
        notification.email_attempts += 1
        notification.email_last_error = _safe_error(exc)[:300]
        notification.save(
            update_fields=[
                "email_status",
                "email_attempts",
                "email_last_error",
            ]
        )
        logger.error(
            "Notification email delivery failed for notification id %s "
            "(type=%s attempt=%s).",
            notification.pk,
            type(exc).__name__,
            notification.email_attempts,
        )
        return False

    notification.email_status = Notification.EmailStatus.SENT
    notification.email_attempts += 1
    notification.email_sent_at = timezone.now()
    notification.email_last_error = ""
    notification.save(
        update_fields=[
            "email_status",
            "email_attempts",
            "email_sent_at",
            "email_last_error",
        ]
    )
    logger.info(
        "Notification email delivered for notification id %s.", notification.pk
    )
    return True
