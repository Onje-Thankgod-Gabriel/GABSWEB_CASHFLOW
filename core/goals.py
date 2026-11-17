"""Authoritative Budget-to-Goals calculations.

Goals funding is derived only from the user's non-cancelled Budget
allocations in the Goals category. Assignments are planning records and never
create income, expenses, transfers, Savings contributions or account changes.
"""

import threading
from contextlib import contextmanager
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Sum
from django.utils import timezone

from . import financials
from .models import BudgetAllocation, Goal, GoalAssignment, ZERO_MONEY

GOALS_CATEGORY_NAME = "Goals"

FUNDING_EXCEEDED_MESSAGE = (
    "This change would use more money than is available for your Goals. "
    "Reduce the assignment or add more money to your Goals budget before "
    "trying again."
)
BLOCKED_PURCHASE_REMOVAL_MESSAGE = (
    "Some of this Goal's money has already been assigned to another Goal. "
    "Remove that assignment first, then try deleting this expense again."
)

_owner_locks = {}
_owner_locks_guard = threading.Lock()


@contextmanager
def goal_write_lock(owner_id):
    """Serialize one user's Goals writes inside this process.

    The SQLite configuration uses IMMEDIATE write transactions so separate
    worker processes also serialize funding checks and writes. Production on
    a database with row-level locking can additionally use select_for_update.
    """
    key = str(owner_id)
    with _owner_locks_guard:
        lock = _owner_locks.setdefault(key, threading.RLock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def is_goals_category(name):
    return " ".join((name or "").split()).lower() == GOALS_CATEGORY_NAME.lower()


def allocation_counts_as_goals(allocation):
    return (
        allocation is not None
        and allocation.status != BudgetAllocation.Status.CANCELLED
        and is_goals_category(allocation.category.name)
    )


def goals_allocations(owner):
    return BudgetAllocation.objects.filter(
        budget__owner=owner,
        category__name__iexact=GOALS_CATEGORY_NAME,
    ).exclude(status=BudgetAllocation.Status.CANCELLED)


def total_goals_funding(owner):
    total = goals_allocations(owner).aggregate(total=Sum("planned_amount"))[
        "total"
    ]
    return financials.quantize_money(total or ZERO_MONEY)


def active_assignments(owner):
    """Return assignments that still reserve available Goals funding."""
    return GoalAssignment.objects.filter(
        owner=owner,
        reversed_at__isnull=True,
        released_at__isnull=True,
    )


def total_assigned(owner, exclude_pk=None):
    """Return funding still reserved by active, unreleased assignments."""
    queryset = active_assignments(owner)
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    total = queryset.aggregate(total=Sum("amount"))["total"]
    return financials.quantize_money(total or ZERO_MONEY)


def goal_assigned_total(goal, *, include_released=True):
    """Return the goal's historical assignment total.

    Manual reversals are excluded. Assignments released by a recorded
    purchase remain part of the goal's history, so they are included by
    default and shown as the original assigned amount.
    """
    queryset = GoalAssignment.objects.filter(
        owner=goal.owner,
        goal=goal,
        reversed_at__isnull=True,
    )
    if not include_released:
        queryset = queryset.filter(released_at__isnull=True)
    total = queryset.aggregate(total=Sum("amount"))["total"]
    return financials.quantize_money(total or ZERO_MONEY)


def goal_reserved_total(goal):
    """Return funding still reserved for this goal.

    A recorded purchase consumes part of the assignment and returns the unused
    part to the pool, so a purchased goal reserves nothing.
    """
    if purchase_for_goal(goal) is not None:
        return ZERO_MONEY
    return goal_assigned_total(goal, include_released=False)


def goal_consumed_total(goal):
    """Return the assigned funding consumed by the recorded purchase."""
    purchase = purchase_for_goal(goal)
    if purchase is None:
        return ZERO_MONEY
    assigned = goal_assigned_total(goal)
    purchase_amount = financials.quantize_money(purchase.amount)
    return financials.quantize_money(min(assigned, purchase_amount))


def goal_released_total(goal):
    """Return the unused assignment funding released by the purchase."""
    purchase = purchase_for_goal(goal)
    if purchase is None:
        return ZERO_MONEY
    assigned = goal_assigned_total(goal)
    purchase_amount = financials.quantize_money(purchase.amount)
    return financials.quantize_money(max(ZERO_MONEY, assigned - purchase_amount))


def goal_is_fully_funded(goal):
    return goal_assigned_total(goal) >= financials.quantize_money(
        goal.target_amount
    )


def goal_has_released_funding(goal):
    return GoalAssignment.objects.filter(
        owner=goal.owner,
        goal=goal,
        reversed_at__isnull=True,
        released_at__isnull=False,
    ).exists()


def purchase_for_goal(goal):
    """Return the linked purchase transaction, or None."""
    try:
        return goal.purchase_transaction
    except ObjectDoesNotExist:
        return None


def goal_status(goal):
    if purchase_for_goal(goal) is not None:
        return "purchased"
    if goal_is_fully_funded(goal):
        return "fully_funded"
    return "active"


def purchase_link_error(goal, owner, *, transaction=None):
    """Return a clear error when an expense may not purchase this goal."""
    if goal.owner_id != owner.pk:
        return "Select one of your own goals."
    purchase = purchase_for_goal(goal)
    current_id = transaction.pk if transaction is not None else None
    if purchase is not None and purchase.pk != current_id:
        return "This goal already has a recorded purchase."
    if goal_has_released_funding(goal):
        return (
            "This goal already has released funding. The previous purchase "
            "must be removed before linking another expense."
        )
    if not goal_is_fully_funded(goal):
        return "Only fully funded goals can be linked to a purchase."
    return None


def funding_shortfall(owner, *, assigned=None, consumed=None):
    """Return how far a projected committed total exceeds cumulative funding.

    ``assigned`` and ``consumed`` can be supplied as projected values. A
    positive result means the operation would break the authoritative funding
    invariant and must be refused.
    """
    funding = total_goals_funding(owner)
    projected_assigned = (
        total_assigned(owner)
        if assigned is None
        else financials.quantize_money(assigned)
    )
    projected_consumed = (
        total_consumed(owner)
        if consumed is None
        else financials.quantize_money(consumed)
    )
    committed = financials.quantize_money(
        projected_assigned + projected_consumed
    )
    return financials.quantize_money(
        max(ZERO_MONEY, committed - funding)
    )


def purchase_amount_change_error(goal, new_amount):
    """Return an error when raising a linked purchase would over-commit funding.

    Increasing the actual expense consumes more of the assignment and reclaims
    part of the funding that the purchase had released. That reclaimed amount
    may already have been assigned to other goals, so the change is refused
    unless the projected committed funding still fits the cumulative pool.
    """
    if purchase_for_goal(goal) is None:
        return None
    assigned = goal_assigned_total(goal)
    current_consumed = goal_consumed_total(goal)
    new_consumed = financials.quantize_money(
        min(assigned, financials.quantize_money(new_amount))
    )
    extra_consumed = financials.quantize_money(new_consumed - current_consumed)
    if extra_consumed <= ZERO_MONEY:
        return None
    projected_consumed = financials.quantize_money(
        total_consumed(goal.owner) - current_consumed + new_consumed
    )
    shortfall = funding_shortfall(
        goal.owner, consumed=projected_consumed
    )
    if shortfall <= ZERO_MONEY:
        return None
    return FUNDING_EXCEEDED_MESSAGE


def total_consumed(owner):
    """Return assigned funding consumed by all recorded purchases."""
    goals = (
        Goal.objects.filter(owner=owner, purchase_transaction__isnull=False)
        .select_related("purchase_transaction")
        .prefetch_related("assignments")
    )
    total = sum((goal_consumed_total(goal) for goal in goals), ZERO_MONEY)
    return financials.quantize_money(total)


def committed_funding(owner):
    """Return funding that may not be reassigned.

    This is active reserved funding plus funding already consumed by recorded
    goal purchases. Unused purchase funding is excluded because it has already
    returned to the available pool.
    """
    return financials.quantize_money(
        total_assigned(owner) + total_consumed(owner)
    )


def release_goal_for_purchase(goal, *, at=None):
    """Mark every active assignment as applied to a recorded purchase.

    Purchase funding is consumed only up to the actual expense amount. The
    unused part of the assignment is released back to the available pool and
    is calculated from the purchase transaction. This is a planning
    adjustment only; it does not create income, an expense, a transfer, a
    Savings movement or an account balance change.
    """
    queryset = GoalAssignment.objects.filter(
        owner=goal.owner,
        goal=goal,
        reversed_at__isnull=True,
        released_at__isnull=True,
    )
    released_at = at or timezone.now()
    queryset.update(released_at=released_at)
    return goal_released_total(goal)


def restore_goal_funding_for_unlink(goal):
    """Return purchase-applied assignments to reserved funding.

    Unlinking removes the purchase, so consumed funding becomes assigned again
    and the unused amount that had returned to the pool is reclaimed. Returns
    an error string when that unused funding has since been assigned elsewhere.
    Historical assignment rows are never deleted or rewritten; only the
    purchase marker is cleared when the restoration is safe.
    """
    released_rows = list(
        GoalAssignment.objects.filter(
            owner=goal.owner,
            goal=goal,
            reversed_at__isnull=True,
            released_at__isnull=False,
        )
    )
    if not released_rows:
        return None
    restore_amount = financials.quantize_money(
        sum((item.amount for item in released_rows), ZERO_MONEY)
    )
    consumed_for_goal = goal_consumed_total(goal)
    projected_assigned = financials.quantize_money(
        total_assigned(goal.owner) + restore_amount
    )
    projected_consumed = financials.quantize_money(
        total_consumed(goal.owner) - consumed_for_goal
    )
    shortfall = funding_shortfall(
        goal.owner,
        assigned=projected_assigned,
        consumed=projected_consumed,
    )
    if shortfall > ZERO_MONEY:
        return BLOCKED_PURCHASE_REMOVAL_MESSAGE
    GoalAssignment.objects.filter(
        pk__in=[item.pk for item in released_rows]
    ).update(released_at=None)
    return None


def available_funding(owner, exclude_pk=None):
    """Return unassigned Goals funding, never below zero.

    Cumulative allocations are split between active reserved assignments and
    funding consumed by completed purchases. Any unused purchase funding is
    already part of this available amount.
    """
    difference = (
        total_goals_funding(owner)
        - total_assigned(owner, exclude_pk=exclude_pk)
        - total_consumed(owner)
    )
    return financials.quantize_money(max(ZERO_MONEY, difference))


def projected_total_goals_funding(
    owner,
    *,
    existing=None,
    new_amount=None,
    new_category_name=None,
    new_status=None,
    remove=False,
):
    """Return total Goals funding if one Budget allocation changes."""
    total = total_goals_funding(owner)
    if allocation_counts_as_goals(existing):
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
        if is_goals_category(category_name) and status != BudgetAllocation.Status.CANCELLED:
            total += financials.quantize_money(amount)
    return financials.quantize_money(total)


def allocation_change_error(owner, candidate_total):
    """Return a clear error when a Budget change would strand funding."""
    committed = committed_funding(owner)
    candidate_total = financials.quantize_money(candidate_total)
    if candidate_total >= committed:
        return None
    return (
        "This Budget change would reduce total Goals funding to "
        f"₦{candidate_total:,.2f}, but ₦{committed:,.2f} is already "
        "assigned to active goals or used by recorded purchases. Increase "
        "the Goals allocation, or reverse an assignment before reducing it."
    )
