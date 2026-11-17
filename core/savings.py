"""Authoritative Budget-to-Savings calculations.

Monthly savings are never stored as separate contribution records. They are
derived from the user's non-cancelled Budget allocations in the Savings
category, so editing or removing an allocation can never create a duplicate.
Withdrawals are the only persisted savings records.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Sum

from . import financials
from .models import BudgetAllocation, SavingsWithdrawal, ZERO_MONEY

SAVINGS_CATEGORY_NAME = "Savings"


def is_savings_category(name):
    return " ".join((name or "").split()).lower() == SAVINGS_CATEGORY_NAME.lower()


def allocation_counts_as_savings(allocation):
    return (
        allocation is not None
        and allocation.status != BudgetAllocation.Status.CANCELLED
        and is_savings_category(allocation.category.name)
    )


def savings_allocations(owner):
    return BudgetAllocation.objects.filter(
        budget__owner=owner,
        category__name__iexact=SAVINGS_CATEGORY_NAME,
    ).exclude(status=BudgetAllocation.Status.CANCELLED)


def monthly_savings(owner):
    """Return {first-of-month date: saved amount} from Budget allocations."""
    rows = (
        savings_allocations(owner)
        .values("budget__month")
        .annotate(total=Sum("planned_amount"))
        .order_by("budget__month")
    )
    return {
        row["budget__month"]: financials.quantize_money(
            row["total"] or ZERO_MONEY
        )
        for row in rows
    }


def total_saved(owner):
    return financials.quantize_money(
        sum(monthly_savings(owner).values(), ZERO_MONEY)
    )


def total_withdrawn(owner, exclude_pk=None):
    queryset = SavingsWithdrawal.objects.filter(owner=owner)
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    total = queryset.aggregate(total=Sum("amount"))["total"]
    return financials.quantize_money(total or ZERO_MONEY)


def current_balance(owner, exclude_pk=None):
    return financials.quantize_money(
        total_saved(owner) - total_withdrawn(owner, exclude_pk=exclude_pk)
    )


def monthly_withdrawn(owner, month):
    total = SavingsWithdrawal.objects.filter(
        owner=owner,
        date__gte=month,
        date__lt=financials.next_month_start(month),
    ).aggregate(total=Sum("amount"))["total"]
    return financials.quantize_money(total or ZERO_MONEY)


def month_starts(owner, selected_month=None):
    """Return every month relevant to savings history, newest first."""
    months = set(monthly_savings(owner))
    withdrawal_dates = SavingsWithdrawal.objects.filter(
        owner=owner
    ).values_list("date", flat=True)
    months.update(value.replace(day=1) for value in withdrawal_dates)
    months.add(date.today().replace(day=1))
    if selected_month is not None:
        months.add(selected_month)
    return sorted(months, reverse=True)


def projected_total_saved(
    owner,
    *,
    existing=None,
    new_amount=None,
    new_category_name=None,
    new_status=None,
    remove=False,
):
    """Return total saved if one allocation is created, edited or removed."""
    total = total_saved(owner)
    if allocation_counts_as_savings(existing):
        total -= financials.quantize_money(existing.planned_amount)
    if not remove:
        category_name = (
            new_category_name
            if new_category_name is not None
            else (existing.category.name if existing is not None else "")
        )
        status = (
            new_status
            if new_status is not None
            else (
                existing.status
                if existing is not None
                else BudgetAllocation.Status.ACTIVE
            )
        )
        amount = (
            new_amount
            if new_amount is not None
            else (existing.planned_amount if existing is not None else ZERO_MONEY)
        )
        if is_savings_category(category_name) and status != BudgetAllocation.Status.CANCELLED:
            total += financials.quantize_money(amount)
    return financials.quantize_money(total)


def allocation_change_error(owner, candidate_total):
    """Return a clear error when a Budget change would break withdrawals."""
    withdrawn = total_withdrawn(owner)
    if candidate_total >= withdrawn:
        return None
    return (
        "This Budget change would reduce total Savings to "
        f"{financials.quantize_money(candidate_total):,.2f}, but recorded "
        f"withdrawals already total {withdrawn:,.2f}. Reduce or delete "
        "withdrawals first, or keep enough Savings allocation."
    )
