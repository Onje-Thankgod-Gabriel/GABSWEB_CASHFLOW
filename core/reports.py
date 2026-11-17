"""Authoritative report composition for the Reports page and PDF export.

Reports are read-only. Every figure is derived from the same server-side
models and helpers used by Budget, Savings, Goals and the transaction ledger.
Nothing in this module creates, edits or deletes financial records.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Sum
from django.utils.html import escape

from . import financials, goals as goals_calc, savings as savings_calc
from .models import (
    Account,
    BudgetAllocation,
    Goal,
    SavingsWithdrawal,
    Transaction,
    ZERO_MONEY,
)

MONTHS_SHORT = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def parse_month(value):
    """Parse ``YYYY-MM`` into the first day of that month, or return None."""
    if not value:
        return None
    parts = str(value).split("-")
    if len(parts) != 2:
        return None
    try:
        year, month = (int(part) for part in parts)
        if year < 1900 or year > 2200:
            return None
        return date(year, month, 1)
    except (TypeError, ValueError):
        return None


def parse_year(value):
    if not value:
        return None
    try:
        year = int(str(value))
    except (TypeError, ValueError):
        return None
    if year < 1900 or year > 2200:
        return None
    return year


def month_key(month):
    return month.strftime("%Y-%m")


def shift_month(month, offset):
    index = (month.year * 12 + month.month - 1) + offset
    return date(index // 12, index % 12 + 1, 1)


def _today():
    return date.today()


def _money(value):
    return "₦" + f"{Decimal(value or 0):,.2f}"


def _percent(value):
    if value is None:
        return "—"
    return f"{Decimal(value):,.1f}%"


def _sum(rows, attribute="amount"):
    total = ZERO_MONEY
    for row in rows:
        value = (
            row.get(attribute, ZERO_MONEY)
            if isinstance(row, dict)
            else getattr(row, attribute, ZERO_MONEY)
        )
        total += value or ZERO_MONEY
    return financials.quantize_money(total)


def _month_starts(start, end, limit_future=False):
    months = []
    cursor = start.replace(day=1)
    last = end - timedelta(days=1)
    today = _today()
    if limit_future and last > today:
        last = date(today.year, today.month, 1)
    while cursor <= last:
        months.append(cursor)
        cursor = shift_month(cursor, 1)
    return months


def _label_for_date(value):
    return MONTHS_SHORT[value.month - 1] + " " + str(value.day)


def _label_for_month(value):
    return MONTHS_SHORT[value.month - 1] + " " + str(value.year)[2:]


def period_bounds(mode, key):
    """Return start/end/previous bounds for a monthly or yearly report."""
    if mode == "year":
        year = parse_year(key)
        if year is None:
            return None
        start = date(year, 1, 1)
        end = date(year + 1, 1, 1)
        previous_start = date(year - 1, 1, 1)
        previous_end = start
        label = str(year)
        previous_label = str(year - 1)
        comparison = "Compared with the previous year"
    else:
        month = parse_month(key)
        if month is None:
            return None
        start = month
        end = financials.next_month_start(start)
        previous_start = shift_month(start, -1)
        previous_end = start
        label = start.strftime("%B %Y")
        previous_label = previous_start.strftime("%B %Y")
        comparison = "Compared with the previous month"
    return {
        "mode": mode,
        "start": start,
        "end": end,
        "display_end": end - timedelta(days=1),
        "previous_start": previous_start,
        "previous_end": previous_end,
        "label": label,
        "previous_label": previous_label,
        "comparison": comparison,
        "key": key,
    }


def _account_balances(owner, start, end):
    accounts = list(Account.objects.filter(owner=owner).order_by("name"))
    transactions = list(
        Transaction.objects.filter(owner=owner, date__lt=end).select_related(
            "account", "destination_account"
        )
    )
    before_period = [entry for entry in transactions if entry.date < start]

    rows = []
    total_opening = ZERO_MONEY
    total_closing = ZERO_MONEY
    for account in accounts:
        opening = financials.quantize_money(
            account.calculate_balance(before_period)
        )
        closing = financials.quantize_money(
            account.calculate_balance(transactions)
        )
        total_opening += opening
        total_closing += closing
        rows.append(
            {
                "id": str(account.pk),
                "name": account.name,
                "type": account.get_account_type_display(),
                "status": account.status,
                "opening": opening,
                "closing": closing,
                "movement": financials.quantize_money(closing - opening),
            }
        )

    return {
        "rows": rows,
        "count": len(rows),
        "total_opening": financials.quantize_money(total_opening),
        "total_closing": financials.quantize_money(total_closing),
        "total_movement": financials.quantize_money(total_closing - total_opening),
    }


def _period_transactions(owner, start, end):
    return list(
        Transaction.objects.filter(
            owner=owner, date__gte=start, date__lt=end
        ).select_related("account", "destination_account")
    )


def _cash_flow(entries):
    income_entries = [
        entry
        for entry in entries
        if entry.transaction_type == Transaction.TransactionType.INCOME
    ]
    expense_entries = [
        entry
        for entry in entries
        if entry.transaction_type == Transaction.TransactionType.EXPENSE
    ]
    transfers = [
        entry
        for entry in entries
        if entry.transaction_type == Transaction.TransactionType.TRANSFER
    ]
    income = _sum(income_entries)
    expenses = _sum(expense_entries)
    linked = _sum(
        [entry for entry in expense_entries if entry.allocation_id is not None]
    )
    return {
        "income": income,
        "expenses": expenses,
        "net": financials.quantize_money(income - expenses),
        "transfer_in": _sum(transfers),
        "transfer_out": _sum(transfers),
        "transfer_count": len(transfers),
        "transfers": [
            {
                "date": entry.date,
                "amount": financials.quantize_money(entry.amount),
                "from": entry.account.name if entry.account_id else "",
                "to": (
                    entry.destination_account.name
                    if entry.destination_account_id
                    else ""
                ),
                "description": entry.description,
            }
            for entry in transfers
        ],
        "transaction_count": len(entries),
        "income_count": len(income_entries),
        "expense_count": len(expense_entries),
        "linked_expenses": financials.quantize_money(linked),
        "unlinked_expenses": financials.quantize_money(expenses - linked),
    }


def _category_name(entry):
    value = (entry.category or "").strip()
    return value or "Uncategorised"


def _category_breakdown(current_entries, previous_entries, kind):
    current = {}
    previous = {}
    for entry in current_entries:
        if entry.transaction_type != kind:
            continue
        name = _category_name(entry)
        row = current.setdefault(name, {"amount": ZERO_MONEY, "count": 0})
        row["amount"] += entry.amount
        row["count"] += 1
    for entry in previous_entries:
        if entry.transaction_type != kind:
            continue
        name = _category_name(entry)
        row = previous.setdefault(name, {"amount": ZERO_MONEY, "count": 0})
        row["amount"] += entry.amount
        row["count"] += 1

    total = financials.quantize_money(
        sum((row["amount"] for row in current.values()), ZERO_MONEY)
    )
    rows = []
    for name, row in current.items():
        amount = financials.quantize_money(row["amount"])
        previous_amount = financials.quantize_money(
            previous.get(name, {}).get("amount", ZERO_MONEY)
        )
        change = financials.quantize_money(amount - previous_amount)
        change_percent = None
        if previous_amount > ZERO_MONEY:
            change_percent = financials.quantize_money(
                (change / previous_amount) * 100
            )
        percentage = None
        if total > ZERO_MONEY:
            percentage = financials.quantize_money((amount / total) * 100)
        rows.append(
            {
                "category": name,
                "amount": amount,
                "count": row["count"],
                "previousAmount": previous_amount,
                "change": change,
                "changePercent": change_percent,
                "percentage": percentage,
            }
        )
    rows.sort(key=lambda item: item["amount"], reverse=True)
    return {"total": total, "list": rows}


def _series(entries, start, end, mode, months):
    buckets = []
    if mode == "year":
        for month in months:
            bucket_end = financials.next_month_start(month)
            buckets.append(
                {
                    "start": month,
                    "end": bucket_end - timedelta(days=1),
                    "label": _label_for_month(month),
                    "income": ZERO_MONEY,
                    "expenses": ZERO_MONEY,
                    "net": ZERO_MONEY,
                }
            )
        index = {month_key(month): bucket for month, bucket in zip(months, buckets)}
        for entry in entries:
            bucket = index.get(entry.date.strftime("%Y-%m"))
            if bucket is None:
                continue
            if entry.transaction_type == Transaction.TransactionType.INCOME:
                bucket["income"] += entry.amount
            elif entry.transaction_type == Transaction.TransactionType.EXPENSE:
                bucket["expenses"] += entry.amount
        series_mode = "month"
    else:
        cursor = start
        while cursor < end:
            buckets.append(
                {
                    "start": cursor,
                    "end": cursor,
                    "label": _label_for_date(cursor),
                    "income": ZERO_MONEY,
                    "expenses": ZERO_MONEY,
                    "net": ZERO_MONEY,
                }
            )
            cursor += timedelta(days=1)
        index = {bucket["start"]: bucket for bucket in buckets}
        for entry in entries:
            bucket = index.get(entry.date)
            if bucket is None:
                continue
            if entry.transaction_type == Transaction.TransactionType.INCOME:
                bucket["income"] += entry.amount
            elif entry.transaction_type == Transaction.TransactionType.EXPENSE:
                bucket["expenses"] += entry.amount
        series_mode = "day"

    for bucket in buckets:
        bucket["income"] = financials.quantize_money(bucket["income"])
        bucket["expenses"] = financials.quantize_money(bucket["expenses"])
        bucket["net"] = financials.quantize_money(
            bucket["income"] - bucket["expenses"]
        )
    return {"mode": series_mode, "buckets": buckets}


def _budget_period(owner, start, end, months):
    monthly = []
    rows = []
    total_allocated = ZERO_MONEY
    total_actual = ZERO_MONEY
    total_unallocated = ZERO_MONEY
    total_over_allocated = ZERO_MONEY
    for month in months:
        summary = financials.summarize_month(owner, month)
        total_allocated += summary["total_allocated"]
        total_actual += summary["total_actual_spending"]
        total_unallocated += summary["unallocated"]
        total_over_allocated += summary["over_allocated"]
        monthly.append(
            {
                "month": month_key(month),
                "allocated": summary["total_allocated"],
                "actual": summary["total_actual_spending"],
                "unallocated": summary["unallocated"],
                "over_allocated": summary["over_allocated"],
                "remaining": summary["total_remaining"],
                "allocation_count": summary["allocation_count"],
            }
        )
        for item in summary["allocations"]:
            allocation = item["allocation"]
            usage = None
            if item["planned"] > ZERO_MONEY:
                usage = financials.quantize_money(
                    (item["actual"] / item["planned"]) * 100
                )
            rows.append(
                {
                    "id": str(allocation.pk),
                    "name": allocation.name,
                    "category": allocation.category.name,
                    "financialMonth": month_key(month),
                    "planned": item["planned"],
                    "actual": item["actual"],
                    "remaining": item["remaining"],
                    "over_spent": item["over_spent"],
                    "usage": usage,
                    "status": allocation.status,
                }
            )

    total_allocated = financials.quantize_money(total_allocated)
    total_actual = financials.quantize_money(total_actual)
    return {
        "rows": rows,
        "monthly": monthly,
        "total_allocated": total_allocated,
        "total_actual": total_actual,
        "total_remaining": financials.quantize_money(
            total_allocated - total_actual
        ),
        "unallocated": financials.quantize_money(total_unallocated),
        "over_allocated": financials.quantize_money(total_over_allocated),
        "allocation_count": len(rows),
        "month_count": len(months),
    }


def _savings_period(owner, start, end, months):
    monthly = savings_calc.monthly_savings(owner)
    withdrawals = list(
        SavingsWithdrawal.objects.filter(owner=owner).order_by(
            "-date", "-created_at"
        )
    )
    by_month = {}
    for withdrawal in withdrawals:
        by_month.setdefault(withdrawal.date.replace(day=1), []).append(withdrawal)

    month_rows = []
    contributions = ZERO_MONEY
    contribution_count = 0
    withdrawal_total = ZERO_MONEY
    withdrawal_count = 0
    for month in months:
        saved = financials.quantize_money(monthly.get(month, ZERO_MONEY))
        month_withdrawals = [
            item
            for item in by_month.get(month, [])
            if start <= item.date < end
        ]
        withdrawn = _sum(month_withdrawals)
        if saved > ZERO_MONEY:
            contribution_count += 1
        contributions += saved
        withdrawal_total += withdrawn
        withdrawal_count += len(month_withdrawals)
        month_rows.append(
            {
                "month": month_key(month),
                "saved": saved,
                "withdrawn": withdrawn,
                "net": financials.quantize_money(saved - withdrawn),
                "withdrawals": [
                    {
                        "id": str(item.pk),
                        "amount": financials.quantize_money(item.amount),
                        "date": item.date,
                        "reason": item.reason,
                    }
                    for item in month_withdrawals
                ],
            }
        )

    period_withdrawals = [
        item for item in withdrawals if start <= item.date < end
    ]
    cumulative_saved = financials.quantize_money(
        sum(
            (amount for key, amount in monthly.items() if key < end),
            ZERO_MONEY,
        )
    )
    cumulative_withdrawn = _sum(
        [item for item in withdrawals if item.date < end]
    )
    allocations = list(
        BudgetAllocation.objects.filter(
            budget__owner=owner,
            budget__month__gte=start,
            budget__month__lt=end,
            category__name__iexact=savings_calc.SAVINGS_CATEGORY_NAME,
        )
        .exclude(status=BudgetAllocation.Status.CANCELLED)
        .select_related("category")
        .order_by("budget__month", "name")
    )
    return {
        "available": True,
        "error": False,
        "allocations": [
            {
                "name": allocation.name,
                "month": month_key(allocation.budget.month),
                "amount": financials.quantize_money(allocation.planned_amount),
                "status": allocation.status,
            }
            for allocation in allocations
        ],
        "allocation_total": _sum(allocations, "planned_amount"),
        "allocation_count": len(allocations),
        "contributions": financials.quantize_money(contributions),
        "contributionCount": contribution_count,
        "withdrawalTotal": financials.quantize_money(withdrawal_total),
        "withdrawalCount": withdrawal_count,
        "net": financials.quantize_money(contributions - withdrawal_total),
        "months": month_rows,
        "withdrawals": [
            {
                "id": str(item.pk),
                "amount": financials.quantize_money(item.amount),
                "date": item.date,
                "reason": item.reason,
            }
            for item in period_withdrawals
        ],
        "cumulative_saved": cumulative_saved,
        "cumulative_withdrawn": cumulative_withdrawn,
        "cumulative_balance": financials.quantize_money(
            cumulative_saved - cumulative_withdrawn
        ),
        "totalBalance": financials.quantize_money(
            cumulative_saved - cumulative_withdrawn
        ),
        "current_balance": savings_calc.current_balance(owner),
    }


def _as_of_assignments(goal, end):
    assigned = ZERO_MONEY
    reserved = ZERO_MONEY
    for assignment in goal.assignments.all():
        if assignment.created_at.date() >= end:
            continue
        if assignment.reversed_at is not None and assignment.reversed_at.date() < end:
            continue
        assigned += assignment.amount
        released = (
            assignment.released_at is not None
            and assignment.released_at.date() < end
        )
        if not released:
            reserved += assignment.amount
    return (
        financials.quantize_money(assigned),
        financials.quantize_money(reserved),
    )


def _goals_period(owner, start, end):
    funding_total = financials.quantize_money(
        BudgetAllocation.objects.filter(
            budget__owner=owner,
            budget__month__lt=end,
            category__name__iexact=goals_calc.GOALS_CATEGORY_NAME,
        )
        .exclude(status=BudgetAllocation.Status.CANCELLED)
        .aggregate(total=Sum("planned_amount"))["total"]
        or ZERO_MONEY
    )
    period_funding = financials.quantize_money(
        BudgetAllocation.objects.filter(
            budget__owner=owner,
            budget__month__gte=start,
            budget__month__lt=end,
            category__name__iexact=goals_calc.GOALS_CATEGORY_NAME,
        )
        .exclude(status=BudgetAllocation.Status.CANCELLED)
        .aggregate(total=Sum("planned_amount"))["total"]
        or ZERO_MONEY
    )

    assignments = list(
        goals_calc.GoalAssignment.objects.filter(owner=owner).select_related("goal")
    )
    month_assignments = [
        item for item in assignments if start <= item.created_at.date() < end
    ]
    month_reversals = [
        item
        for item in assignments
        if item.reversed_at is not None
        and start <= item.reversed_at.date() < end
    ]
    month_releases = [
        item
        for item in assignments
        if item.released_at is not None
        and start <= item.released_at.date() < end
    ]

    goals = list(
        Goal.objects.filter(owner=owner)
        .prefetch_related("assignments")
        .select_related("purchase_transaction")
        .order_by("created_at", "name")
    )
    progress_rows = []
    total_assigned = ZERO_MONEY
    total_reserved = ZERO_MONEY
    total_consumed = ZERO_MONEY
    total_released = ZERO_MONEY
    purchased_count = 0
    period_purchases = []

    for goal in goals:
        assigned, reserved = _as_of_assignments(goal, end)
        try:
            purchase = goal.purchase_transaction
        except ObjectDoesNotExist:
            purchase = None
        purchased_as_of = purchase is not None and purchase.date < end
        purchase_amount = (
            financials.quantize_money(purchase.amount)
            if purchased_as_of
            else ZERO_MONEY
        )
        consumed = (
            financials.quantize_money(min(assigned, purchase_amount))
            if purchased_as_of
            else ZERO_MONEY
        )
        released = (
            financials.quantize_money(max(ZERO_MONEY, assigned - purchase_amount))
            if purchased_as_of
            else ZERO_MONEY
        )
        target = financials.quantize_money(goal.target_amount)
        progress = None
        if target > ZERO_MONEY:
            progress = min(
                Decimal("100.00"),
                financials.quantize_money((assigned / target) * 100),
            )
        if purchased_as_of:
            status = "purchased"
            purchased_count += 1
        elif assigned >= target:
            status = "fully funded"
        else:
            status = "in progress"

        if purchase is not None and start <= purchase.date < end:
            period_purchases.append(
                {
                    "goal": goal.name,
                    "date": purchase.date,
                    "amount": purchase_amount,
                }
            )

        total_assigned += assigned
        total_reserved += reserved
        total_consumed += consumed
        total_released += released
        progress_rows.append(
            {
                "name": goal.name,
                "target": target,
                "assigned": assigned,
                "reserved": reserved,
                "consumed": consumed,
                "released": released,
                "progress": progress,
                "status": status,
                "target_date": goal.target_date,
            }
        )

    return {
        "funding_total": funding_total,
        "period_funding": period_funding,
        "monthly_funding": period_funding,
        "assigned_total": financials.quantize_money(total_assigned),
        "reserved_total": financials.quantize_money(total_reserved),
        "consumed_total": financials.quantize_money(total_consumed),
        "released_total": financials.quantize_money(total_released),
        "available": financials.quantize_money(
            max(ZERO_MONEY, funding_total - total_reserved - total_consumed)
        ),
        "month_assignments": _sum(month_assignments),
        "month_assignment_count": len(month_assignments),
        "month_reversals": _sum(month_reversals),
        "month_reversal_count": len(month_reversals),
        "month_releases": _sum(month_releases),
        "month_release_count": len(month_releases),
        "month_purchases": period_purchases,
        "month_purchase_total": _sum(period_purchases),
        "purchased_count": purchased_count,
        "goal_count": len(goals),
        "rows": progress_rows,
    }


def _category_changes(current, previous):
    names = {}
    for row in current:
        names.setdefault(row["category"], {"current": ZERO_MONEY, "previous": ZERO_MONEY})
        names[row["category"]]["current"] = row["amount"]
    for row in previous:
        names.setdefault(row["category"], {"current": ZERO_MONEY, "previous": ZERO_MONEY})
        names[row["category"]]["previous"] = row["amount"]
    rows = []
    for name, values in names.items():
        change = financials.quantize_money(values["current"] - values["previous"])
        if values["current"] == ZERO_MONEY and values["previous"] == ZERO_MONEY:
            continue
        rows.append(
            {
                "category": name,
                "current": values["current"],
                "previous": values["previous"],
                "change": change,
            }
        )
    rows.sort(key=lambda item: abs(item["change"]), reverse=True)
    return rows[:8]


def _comparison(current, previous, categories, previous_categories, savings, previous_savings):
    items = [
        {
            "label": "Income",
            "current": current["income"],
            "previous": previous["income"],
            "favourableWhenUp": True,
        },
        {
            "label": "Expenses",
            "current": current["expenses"],
            "previous": previous["expenses"],
            "favourableWhenUp": False,
        },
        {
            "label": "Net cash flow",
            "current": current["net"],
            "previous": previous["net"],
            "favourableWhenUp": True,
        },
        {
            "label": "Savings set aside",
            "current": savings["contributions"],
            "previous": previous_savings["contributions"],
            "favourableWhenUp": True,
        },
    ]
    return {
        "items": items,
        "hasActivity": any(
            item["current"] != ZERO_MONEY or item["previous"] != ZERO_MONEY
            for item in items
        ),
        "categoryChanges": _category_changes(
            categories["expense"]["list"], previous_categories["expense"]["list"]
        ),
    }


def _insights(report):
    insights = []
    top_expense = report["categories"]["expense"]["list"]
    top_income = report["categories"]["income"]["list"]
    if top_expense:
        item = top_expense[0]
        insights.append(
            {
                "icon": "trending-down",
                "tone": "expense",
                "title": "Highest spending category",
                "text": (
                    "<strong>"
                    + escape(item["category"])
                    + "</strong> accounts for "
                    + _percent(item["percentage"])
                    + " of expenses ("
                    + _money(item["amount"])
                    + ") across "
                    + str(item["count"])
                    + " expense"
                    + ("" if item["count"] == 1 else "s")
                    + "."
                ),
            }
        )
    if top_income:
        item = top_income[0]
        insights.append(
            {
                "icon": "trending-up",
                "tone": "income",
                "title": "Largest income source",
                "text": (
                    "<strong>"
                    + escape(item["category"])
                    + "</strong> provided "
                    + _percent(item["percentage"])
                    + " of income ("
                    + _money(item["amount"])
                    + ") across "
                    + str(item["count"])
                    + " income record"
                    + ("" if item["count"] == 1 else "s")
                    + "."
                ),
            }
        )
    allocations = report["allocations"]
    if allocations["totalPlanned"] > ZERO_MONEY:
        over_rows = [
            row for row in allocations["rows"] if row["actual"] > row["planned"]
        ]
        if over_rows:
            over_rows.sort(
                key=lambda row: row["actual"] - row["planned"], reverse=True
            )
            row = over_rows[0]
            insights.append(
                {
                    "icon": "alert-triangle",
                    "tone": "warning",
                    "title": "Allocation over plan",
                    "text": (
                        "<strong>"
                        + escape(row["name"])
                        + "</strong> is over its planned amount by "
                        + _money(row["actual"] - row["planned"])
                        + (
                            ". " + str(len(over_rows)) + " allocations are above plan."
                            if len(over_rows) > 1
                            else "."
                        )
                    ),
                }
            )
        else:
            usage = (
                allocations["totalActual"] / allocations["totalPlanned"]
            ) * 100
            insights.append(
                {
                    "icon": "target",
                    "tone": "",
                    "title": "Allocation use",
                    "text": (
                        "Linked expenses use <strong>"
                        + _percent(usage)
                        + "</strong> of the "
                        + _money(allocations["totalPlanned"])
                        + " planned for this period."
                    ),
                }
            )
    savings = report["savings"]
    if savings["contributions"] > ZERO_MONEY:
        insights.append(
            {
                "icon": "coins",
                "tone": "savings",
                "title": "Savings set aside",
                "text": (
                    "<strong>"
                    + _money(savings["contributions"])
                    + "</strong> was set aside through Budget Savings in this period."
                ),
            }
        )
    if savings["withdrawalTotal"] > ZERO_MONEY:
        insights.append(
            {
                "icon": "arrow-up-right",
                "tone": "warning",
                "title": "Savings withdrawals",
                "text": (
                    "<strong>"
                    + _money(savings["withdrawalTotal"])
                    + "</strong> was withdrawn from savings in this period."
                ),
            }
        )
    previous = report["previous"]
    current = report["current"]
    if previous["expenses"] > ZERO_MONEY:
        change = (
            (current["expenses"] - previous["expenses"])
            / previous["expenses"]
        ) * 100
        insights.append(
            {
                "icon": "trending-up" if change > 0 else "trending-down",
                "tone": "warning" if change > 0 else "income",
                "title": "Spending change",
                "text": (
                    "Overall expenses "
                    + ("increased" if change > 0 else "decreased")
                    + " by <strong>"
                    + _percent(abs(change))
                    + "</strong> compared with the previous period."
                ),
            }
        )
    return insights[:6]


def build_period_report(owner, mode, key):
    """Return the complete read-only report for a monthly or yearly period."""
    bounds = period_bounds(mode, key)
    if bounds is None:
        return None
    start = bounds["start"]
    end = bounds["end"]
    previous_start = bounds["previous_start"]
    previous_end = bounds["previous_end"]
    months = _month_starts(start, end, limit_future=True)
    previous_months = _month_starts(
        previous_start, previous_end, limit_future=False
    )

    entries = _period_transactions(owner, start, end)
    previous_entries = _period_transactions(owner, previous_start, previous_end)
    cash = _cash_flow(entries)
    previous_cash = _cash_flow(previous_entries)
    categories = {
        "expense": _category_breakdown(
            entries, previous_entries, Transaction.TransactionType.EXPENSE
        ),
        "income": _category_breakdown(
            entries, previous_entries, Transaction.TransactionType.INCOME
        ),
    }
    previous_categories = {
        "expense": _category_breakdown(
            previous_entries,
            previous_entries,
            Transaction.TransactionType.EXPENSE,
        ),
        "income": _category_breakdown(
            previous_entries,
            previous_entries,
            Transaction.TransactionType.INCOME,
        ),
    }
    accounts = _account_balances(owner, start, end)
    budget = _budget_period(owner, start, end, months)
    savings = _savings_period(owner, start, end, months)
    previous_savings = _savings_period(
        owner, previous_start, previous_end, previous_months
    )
    goals = _goals_period(owner, start, end)
    comparison = _comparison(
        cash, previous_cash, categories, previous_categories, savings, previous_savings
    )

    report = {
        "mode": mode,
        "month_key": key,
        "key": key,
        "label": bounds["label"],
        "previous_label": bounds["previous_label"],
        "start": start,
        "end": end,
        "display_end": bounds["display_end"],
        "range": {
            "start": start,
            "end": bounds["display_end"],
            "prevStart": previous_start,
            "prevEnd": previous_end - timedelta(days=1),
            "label": (
                bounds["label"]
                if mode == "year"
                else start.strftime("%B %d, %Y")
                + " – "
                + bounds["display_end"].strftime("%B %d, %Y")
            ),
            "comparison": bounds["comparison"],
            "preset": mode,
        },
        "accounts": accounts,
        "cash_flow": cash,
        "current": {
            "income": cash["income"],
            "expenses": cash["expenses"],
            "net": cash["net"],
            "savingsContributions": savings["contributions"],
            "savingsWithdrawals": savings["withdrawalTotal"],
        },
        "previous": {
            "income": previous_cash["income"],
            "expenses": previous_cash["expenses"],
            "net": previous_cash["net"],
            "savingsContributions": previous_savings["contributions"],
            "savingsWithdrawals": previous_savings["withdrawalTotal"],
        },
        "balances": {
            "opening": accounts["total_opening"],
            "closing": accounts["total_closing"],
            "movement": accounts["total_movement"],
        },
        "transfers": {
            "in": cash["transfer_in"],
            "out": cash["transfer_out"],
            "count": cash["transfer_count"],
            "rows": cash["transfers"],
        },
        "series": _series(entries, start, end, mode, months),
        "categories": categories,
        "allocations": {
            "available": True,
            "error": False,
            "rows": budget["rows"],
            "monthly": budget["monthly"],
            "totalPlanned": budget["total_allocated"],
            "totalActual": budget["total_actual"],
            "totalRemaining": budget["total_remaining"],
            "unallocated": budget["unallocated"],
            "overAllocated": budget["over_allocated"],
            "linkedTotal": cash["linked_expenses"],
            "unlinkedTotal": cash["unlinked_expenses"],
            "monthCount": budget["month_count"],
        },
        "budget": budget,
        "savings": savings,
        "goals": goals,
        "comparison": comparison,
        "insights": [],
    }
    report["insights"] = _insights(report)
    return report
