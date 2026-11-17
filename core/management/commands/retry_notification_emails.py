"""Retry notification emails that failed after their approval committed."""

from django.core.management.base import BaseCommand

from core.models import Notification
from core.notifications import MAX_EMAIL_ATTEMPTS, send_notification_email


class Command(BaseCommand):
    help = (
        "Retry pending or failed notification emails. Safe to run repeatedly; "
        "already-sent notifications are skipped and delivery is never duplicated."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum number of notifications to retry (default: 50).",
        )

    def handle(self, *args, **options):
        limit = max(1, options["limit"])
        queryset = (
            Notification.objects.filter(
                email_status__in=[
                    Notification.EmailStatus.PENDING,
                    Notification.EmailStatus.FAILED,
                ],
                email_attempts__lt=MAX_EMAIL_ATTEMPTS,
            )
            .order_by("created_at")[:limit]
        )
        sent = 0
        failed = 0
        for notification in queryset:
            if send_notification_email(notification.pk):
                sent += 1
            else:
                failed += 1
        self.stdout.write(
            f"Notification emails sent: {sent}; still failing: {failed}."
        )
