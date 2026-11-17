"""Authoritative server-side financial calculations.

Every page that needs monthly income, allocation totals, unallocated funds or
over-allocation must use these helpers instead of rebuilding the formulas in
views, JavaScript or future feature modules.
"""

from datetime import date
from decimal import Decimal

from .models import BudgetAllocation, Transaction

ZERO_MONEY = Decimal("0.00")
MONEY_QUANTUM = Decimal("0.01")


def quantize_money(amount):
    """Return a Decimal rounded to the stored currency precision."""
    return Decimal(amount or 0).quantize(MONEY_QUANTUM)


def next_month_start(month):
    if month.month == 12:
        return date(month.year + 1, 1, 1)
    return date(month.year, month.month + 1, 1)


def month_key(month):
    return month.strftime("%Y-%m")


def monthly_income(owner, month):
    """Actual income received during the month from the shared transaction ledger."""
    entries = Transaction.objects.filter(
        owner=owner,
        transaction_type=Transaction.TransactionType.INCOME,
        date__gte=month,
        date__lt=next_month_start(month),
    ).only("amount")
    return quantize_money(sum((entry.amount for entry in entries), ZERO_MONEY))


def active_allocations(allocations):
    """Allocations that count toward a month's planned total."""
    return [
        allocation
        for allocation in allocations
        if allocation.status != BudgetAllocation.Status.CANCELLED
    ]


def allocation_actual_spending(allocation):
    """Actual expenses linked to an allocation, counted once from the ledger."""
    return quantize_money(allocation.calculate_actual_spending())


def summarize_month(owner, month, allocations=None):
    """Return the single authoritative monthly budget summary.

    ``unallocated`` is never negative. Any excess of planned allocations over
    available income is reported separately as ``over_allocated``. Remaining
    allocation amounts may be negative because that represents real
    category-level overspending, not unallocated income.
    """
    if allocations is None:
        allocations = list(
            BudgetAllocation.objects.filter(
                budget__owner=owner, budget__month=month
            ).select_related("budget", "category")
        )

    active = active_allocations(allocations)
    allocation_rows = []
    total_allocated = ZERO_MONEY
    total_actual = ZERO_MONEY
    for allocation in active:
        actual = allocation_actual_spending(allocation)
        planned = quantize_money(allocation.planned_amount)
        total_allocated += planned
        total_actual += actual
        allocation_rows.append(
            {
                "allocation": allocation,
                "planned": planned,
                "actual": actual,
                "remaining": quantize_money(planned - actual),
                "over_spent": quantize_money(max(ZERO_MONEY, actual - planned)),
            }
        )

    income = monthly_income(owner, month)
    difference = quantize_money(income - total_allocated)
    unallocated = max(ZERO_MONEY, difference)
    over_allocated = max(ZERO_MONEY, quantize_money(-difference))

    return {
        "month": month,
        "month_key": month_key(month),
        "monthly_income": income,
        "total_allocated": quantize_money(total_allocated),
        "unallocated": quantize_money(unallocated),
        "over_allocated": quantize_money(over_allocated),
        "total_actual_spending": quantize_money(total_actual),
        "total_remaining": quantize_money(total_allocated - total_actual),
        "allocation_count": len(active),
        "allocations": allocation_rows,
    }
