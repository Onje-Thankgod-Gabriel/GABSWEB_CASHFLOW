"""Admin-only template tags.

Loaded exclusively by templates under ``templates/admin``. No application
template loads this library.
"""

from django import template

from core.models import Account, Goal, Transaction, User

register = template.Library()


@register.inclusion_tag("admin/partials/gabs_stats.html")
def gabs_dashboard_stats():
    return {
        "total_members": User.objects.count(),
        "pending_members": User.objects.filter(
            account_status=User.AccountStatus.PENDING,
            is_staff=False,
            is_superuser=False,
        ).count(),
        "approved_members": User.objects.filter(
            account_status=User.AccountStatus.APPROVED
        ).count(),
        "total_accounts": Account.objects.count(),
        "total_transactions": Transaction.objects.count(),
        "total_goals": Goal.objects.count(),
    }


@register.filter
def apps_in(app_list, labels):
    """Return only the dashboard app cards whose app labels are listed."""
    wanted = {label.strip() for label in labels.split(",") if label.strip()}
    return [app for app in app_list if app["app_label"] in wanted]


@register.filter
def apps_not_in(app_list, labels):
    """Return the dashboard app cards whose app labels are not listed."""
    excluded = {label.strip() for label in labels.split(",") if label.strip()}
    return [app for app in app_list if app["app_label"] not in excluded]
