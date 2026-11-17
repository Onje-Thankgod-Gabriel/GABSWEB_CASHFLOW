import json
from datetime import date
from decimal import Decimal

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from .models import (
    BudgetAllocation,
    BudgetCategory,
    Goal,
    GoalAssignment,
    MonthlyBudget,
    Transaction,
)
from .test_accounts import AccountTestBase
from . import financials
from . import goals as goals_calc
from . import savings as savings_calc


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class GoalsTestBase(AccountTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def post_json(self, url, payload=None):
        return self.client.post(
            url,
            data=json.dumps(payload or {}),
            content_type="application/json",
        )

    def add_allocation(
        self,
        month="2026-11",
        amount="50000.00",
        category_name="Goals",
        status=BudgetAllocation.Status.ACTIVE,
        owner=None,
    ):
        owner = owner or self.user
        year, month_number = (int(part) for part in month.split("-"))
        budget, _created = MonthlyBudget.objects.get_or_create(
            owner=owner, month=date(year, month_number, 1)
        )
        category, _created = BudgetCategory.objects.get_or_create(
            owner=owner, name=category_name
        )
        return BudgetAllocation.objects.create(
            budget=budget,
            category=category,
            name=category_name,
            planned_amount=Decimal(amount),
            status=status,
        )

    def payload(self):
        response = self.client.get(reverse("core:goals_api"))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def create_goal(
        self,
        name="Laptop",
        target="150000.00",
        target_date="2026-12-31",
        description="",
        client_reference=None,
    ):
        payload = {
            "name": name,
            "targetAmount": target,
            "targetDate": target_date,
            "description": description,
        }
        if client_reference:
            payload["clientReference"] = client_reference
        return self.post_json(reverse("core:goal_create"), payload)

    def goal_id(self, name="Laptop", target="150000.00"):
        response = self.create_goal(name=name, target=target)
        self.assertEqual(response.status_code, 201, response.content)
        return next(
            row["id"] for row in response.json()["goals"] if row["name"] == name
        )

    def assign(self, goal_id, amount, client_reference=None):
        payload = {"amount": amount}
        if client_reference:
            payload["clientReference"] = client_reference
        return self.post_json(
            reverse("core:goal_assignments", args=[goal_id]), payload
        )

    def reverse_assignment(self, goal_id, assignment_id):
        return self.post_json(
            reverse(
                "core:goal_assignment_reverse",
                args=[goal_id, assignment_id],
            )
        )

    def goal_row(self, name="Laptop"):
        return next(row for row in self.payload()["goals"] if row["name"] == name)


class GoalsFundingTests(GoalsTestBase):
    def test_no_goals_allocation_means_zero_available(self):
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")

    def test_november_allocation_becomes_available_funding(self):
        self.add_allocation("2026-11", "50000.00")
        summary = self.payload()["summary"]
        self.assertEqual(summary["totalFunding"], "50000.00")
        self.assertEqual(summary["availableFunding"], "50000.00")

    def test_assignment_reduces_available_funding(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        response = self.assign(goal_id, "10000.00")
        self.assertEqual(response.status_code, 201, response.content)
        summary = response.json()["summary"]
        self.assertEqual(summary["totalAssigned"], "10000.00")
        self.assertEqual(summary["availableFunding"], "40000.00")

    def test_december_allocation_accumulates_with_carry_forward(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        self.assign(goal_id, "10000.00")
        self.add_allocation("2026-12", "50000.00")
        summary = self.payload()["summary"]
        self.assertEqual(summary["totalFunding"], "100000.00")
        self.assertEqual(summary["availableFunding"], "90000.00")

    def test_second_assignment_uses_remaining_pool(self):
        self.add_allocation("2026-11", "50000.00")
        self.add_allocation("2026-12", "50000.00")
        goal_id = self.goal_id()
        self.assign(goal_id, "10000.00")
        response = self.assign(goal_id, "20000.00")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["summary"]["availableFunding"], "70000.00")

    def test_multiple_months_accumulate(self):
        self.add_allocation("2026-11", "50000.00")
        self.add_allocation("2026-12", "30000.00")
        self.add_allocation("2027-01", "40000.00")
        self.assertEqual(self.payload()["summary"]["totalFunding"], "120000.00")

    def test_editing_allocation_updates_pool(self):
        allocation = self.add_allocation("2026-11", "50000.00")
        response = self.post_json(
            reverse("core:budget_allocation_detail", args=[allocation.pk]),
            {
                "month": "2026-11",
                "category": "Goals",
                "name": "Goals",
                "planned_amount": "60000.00",
                "status": "active",
                "description": "",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.payload()["summary"]["totalFunding"], "60000.00")

    def test_cancelled_allocation_does_not_fund_goals(self):
        self.add_allocation(
            "2026-11", "50000.00", status=BudgetAllocation.Status.CANCELLED
        )
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")

    def test_savings_allocations_do_not_fund_goals(self):
        self.add_allocation("2026-11", "50000.00", category_name="Savings")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")

    def test_goals_allocations_do_not_count_as_savings(self):
        self.add_allocation("2026-11", "50000.00", category_name="Goals")
        self.assertEqual(savings_calc.total_saved(self.user), Decimal("0.00"))

    def test_reducing_allocation_below_assignments_is_rejected(self):
        allocation = self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        self.assign(goal_id, "30000.00")
        response = self.post_json(
            reverse("core:budget_allocation_detail", args=[allocation.pk]),
            {
                "month": "2026-11",
                "category": "Goals",
                "name": "Goals",
                "planned_amount": "20000.00",
                "status": "active",
                "description": "",
            },
        )
        self.assertEqual(response.status_code, 400, response.content)
        message = response.json()["errors"]["__all__"][0]
        self.assertIn("₦20,000.00", message)
        self.assertIn("₦30,000.00", message)
        self.assertIn("reverse an assignment", message)
        allocation.refresh_from_db()
        self.assertEqual(allocation.planned_amount, Decimal("50000.00"))
        self.assertEqual(self.payload()["summary"]["availableFunding"], "20000.00")

    def test_cancelling_allocation_below_assignments_is_rejected(self):
        allocation = self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        self.assign(goal_id, "30000.00")
        response = self.post_json(
            reverse("core:budget_allocation_status", args=[allocation.pk]),
            {"status": "cancelled"},
        )
        self.assertEqual(response.status_code, 400, response.content)
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, BudgetAllocation.Status.ACTIVE)

    def test_deleting_allocation_below_assignments_is_rejected(self):
        allocation = self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        self.assign(goal_id, "30000.00")
        response = self.post_json(
            reverse("core:budget_allocation_delete", args=[allocation.pk])
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertTrue(BudgetAllocation.objects.filter(pk=allocation.pk).exists())
        self.assertEqual(self.payload()["summary"]["availableFunding"], "20000.00")

    def test_saving_a_valid_goals_allocation_updates_the_pool_once(self):
        response = self.post_json(
            reverse("core:budget_allocations"),
            {
                "month": "2026-11",
                "category": "Goals",
                "name": "Goals",
                "planned_amount": "50000.00",
                "status": "active",
                "description": "",
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.payload()["summary"]["totalFunding"], "50000.00")
        allocation = BudgetAllocation.objects.get()
        update = self.post_json(
            reverse("core:budget_allocation_detail", args=[allocation.pk]),
            {
                "month": "2026-11",
                "category": "Goals",
                "name": "Goals",
                "planned_amount": "50000.00",
                "status": "active",
                "description": "",
            },
        )
        self.assertEqual(update.status_code, 200, update.content)
        self.assertEqual(self.payload()["summary"]["totalFunding"], "50000.00")

    def test_invalid_budget_amounts_return_actionable_field_errors(self):
        response = self.post_json(
            reverse("core:budget_allocations"),
            {
                "month": "2026-11",
                "category": "Goals",
                "name": "Goals",
                "planned_amount": "0.00",
                "status": "active",
                "description": "",
            },
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("planned_amount", response.json()["errors"])
        self.assertIn(
            "greater than zero",
            response.json()["errors"]["planned_amount"][0],
        )

    def test_assigning_above_available_is_rejected(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        response = self.assign(goal_id, "50000.01")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            response.json().get("code"), "goal_funding_exceeded"
        )
        self.assertEqual(
            response.json()["errors"]["amount"],
            [goals_calc.FUNDING_EXCEEDED_MESSAGE],
        )
        self.assertFalse(GoalAssignment.objects.exists())
        self.assertEqual(self.payload()["summary"]["availableFunding"], "50000.00")

    def test_client_supplied_totals_are_ignored(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        response = self.post_json(
            reverse("core:goal_assignments", args=[goal_id]),
            {
                "amount": "60000.00",
                "availableFunding": "999999.00",
                "totalFunding": "999999.00",
            },
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(GoalAssignment.objects.exists())


class GoalBehaviourTests(GoalsTestBase):
    def test_creating_goal_does_not_change_available_funding(self):
        self.add_allocation("2026-11", "50000.00")
        response = self.create_goal()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["summary"]["availableFunding"], "50000.00")
        self.assertFalse(GoalAssignment.objects.exists())

    def test_creating_goal_creates_no_transaction(self):
        self.add_allocation("2026-11", "50000.00")
        self.create_goal()
        self.assertFalse(Transaction.objects.exists())

    def test_target_date_is_optional(self):
        self.add_allocation("2026-11", "50000.00")
        response = self.create_goal(target_date=None)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(response.json()["goals"][0]["targetDate"])

    def test_assignment_updates_only_the_selected_goal(self):
        self.add_allocation("2026-11", "100000.00")
        laptop_id = self.goal_id("Laptop")
        phone_id = self.goal_id("Phone")
        self.assign(laptop_id, "30000.00")
        rows = {row["name"]: row for row in self.payload()["goals"]}
        self.assertEqual(rows["Laptop"]["assignedAmount"], "30000.00")
        self.assertEqual(rows["Phone"]["assignedAmount"], "0.00")

    def test_progress_and_remaining_are_calculated(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id(target="150000.00")
        self.assign(goal_id, "30000.00")
        row = self.goal_row()
        self.assertEqual(row["assignedAmount"], "30000.00")
        self.assertEqual(row["remainingAmount"], "120000.00")
        self.assertEqual(row["progressPercent"], "20.00")
        self.assertFalse(row["reached"])

    def test_progress_never_exceeds_one_hundred_percent(self):
        self.add_allocation("2026-11", "500000.00")
        goal_id = self.goal_id(target="100000.00")
        self.assign(goal_id, "150000.00")
        row = self.goal_row()
        self.assertEqual(row["progressPercent"], "100.00")
        self.assertEqual(row["remainingAmount"], "0.00")
        self.assertTrue(row["reached"])

    def test_changing_target_updates_progress_without_changing_funding(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id(target="150000.00")
        self.assign(goal_id, "30000.00")
        before = GoalAssignment.objects.get()
        response = self.post_json(
            reverse("core:goal_detail", args=[goal_id]),
            {
                "name": "Laptop",
                "targetAmount": "60000.00",
                "targetDate": "2026-12-31",
                "description": "",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        row = self.goal_row()
        self.assertEqual(row["assignedAmount"], "30000.00")
        self.assertEqual(row["remainingAmount"], "30000.00")
        self.assertEqual(row["progressPercent"], "50.00")
        after = GoalAssignment.objects.get()
        self.assertEqual(before.pk, after.pk)
        self.assertEqual(before.amount, after.amount)

    def test_reversing_assignment_restores_available_funding(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        assignment_id = self.assign(goal_id, "30000.00").json()["goals"][0][
            "assignments"
        ][0]["id"]
        response = self.reverse_assignment(goal_id, assignment_id)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["summary"]["availableFunding"], "50000.00")
        self.assertEqual(response.json()["goals"][0]["assignedAmount"], "0.00")
        assignment = GoalAssignment.objects.get(pk=assignment_id)
        self.assertIsNotNone(assignment.reversed_at)

    def test_reversing_an_assignment_twice_is_rejected(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        assignment_id = self.assign(goal_id, "30000.00").json()["goals"][0][
            "assignments"
        ][0]["id"]
        self.assertEqual(
            self.reverse_assignment(goal_id, assignment_id).status_code, 200
        )
        response = self.reverse_assignment(goal_id, assignment_id)
        self.assertEqual(response.status_code, 409, response.content)

    def test_active_goal_cannot_be_deleted(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        self.assign(goal_id, "30000.00")
        response = self.post_json(reverse("core:goal_delete", args=[goal_id]))
        self.assertEqual(response.status_code, 409, response.content)
        self.assertTrue(Goal.objects.filter(pk=goal_id).exists())

    def test_goal_with_no_assignments_can_be_deleted(self):
        goal_id = self.goal_id()
        response = self.post_json(reverse("core:goal_delete", args=[goal_id]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Goal.objects.filter(pk=goal_id).exists())

    def test_goal_with_reversed_history_cannot_be_deleted(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        assignment_id = self.assign(goal_id, "30000.00").json()["goals"][0][
            "assignments"
        ][0]["id"]
        self.reverse_assignment(goal_id, assignment_id)
        response = self.post_json(reverse("core:goal_delete", args=[goal_id]))
        self.assertEqual(response.status_code, 409, response.content)
        self.assertTrue(Goal.objects.filter(pk=goal_id).exists())
        assignment = GoalAssignment.objects.get(pk=assignment_id)
        self.assertIsNotNone(assignment.reversed_at)
        self.assertEqual(assignment.amount, Decimal("30000.00"))
        self.assertIn(
            "funding history",
            response.json()["errors"]["__all__"][0],
        )
        self.assertEqual(self.payload()["summary"]["availableFunding"], "50000.00")

    def test_invalid_amounts_and_dates_are_rejected(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        for payload in (
            {"name": "Laptop", "targetAmount": "0", "targetDate": None},
            {"name": "Laptop", "targetAmount": "-1", "targetDate": None},
            {"name": "Laptop", "targetAmount": "abc", "targetDate": None},
            {"name": "", "targetAmount": "100", "targetDate": None},
            {"name": "Laptop", "targetAmount": "100", "targetDate": "2026-13-01"},
        ):
            response = self.post_json(reverse("core:goal_create"), payload)
            self.assertEqual(response.status_code, 400, response.content)
        for amount in ("0.00", "-5.00", "not-a-number"):
            response = self.assign(goal_id, amount)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(Goal.objects.count(), 1)

    def test_duplicate_client_reference_is_idempotent(self):
        self.add_allocation("2026-11", "50000.00")
        first = self.create_goal(client_reference="goal-1")
        second = self.create_goal(client_reference="goal-1")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(Goal.objects.count(), 1)
        goal_id = first.json()["goals"][0]["id"]
        first_assign = self.assign(goal_id, "10000.00", "assign-1")
        second_assign = self.assign(goal_id, "10000.00", "assign-1")
        self.assertEqual(first_assign.status_code, 201, first_assign.content)
        self.assertEqual(second_assign.status_code, 200, second_assign.content)
        self.assertEqual(GoalAssignment.objects.count(), 1)
        self.assertEqual(self.payload()["summary"]["availableFunding"], "40000.00")


class GoalPurchaseTests(GoalsTestBase):
    PURCHASE_DATE = "2026-11-10"

    def funded_goal(
        self,
        name="Laptop",
        target="150000.00",
        funding="150000.00",
    ):
        self.add_allocation("2026-11", funding)
        goal_id = self.goal_id(name=name, target=target)
        self.assign(goal_id, funding)
        return goal_id

    def spend(
        self,
        account,
        amount,
        goal_id="",
        client_reference=None,
        transaction_type="expense",
        **overrides,
    ):
        payload = {
            "transaction_type": transaction_type,
            "amount": amount,
            "date": self.PURCHASE_DATE,
            "account": str(account.pk),
            "description": "Laptop purchase",
            "goal": str(goal_id) if goal_id else "",
        }
        payload.update(overrides)
        if client_reference:
            payload["clientReference"] = client_reference
        return self.post_json(reverse("core:transactions"), payload)

    def edit_transaction(self, transaction_id, account, amount, goal_id="", **overrides):
        payload = {
            "transaction_type": "expense",
            "amount": amount,
            "date": self.PURCHASE_DATE,
            "account": str(account.pk),
            "description": "Laptop purchase",
            "goal": str(goal_id) if goal_id else "",
        }
        payload.update(overrides)
        return self.post_json(
            reverse("core:transaction_detail", args=[transaction_id]), payload
        )

    def assert_funding_invariant(self):
        summary = self.payload()["summary"]
        self.assertEqual(
            Decimal(summary["totalFunding"]),
            Decimal(summary["availableFunding"])
            + Decimal(summary["totalAssigned"])
            + Decimal(summary["totalConsumed"]),
        )

    def test_fully_funded_goal_records_no_purchase(self):
        goal_id = self.funded_goal()
        row = self.goal_row()
        self.assertEqual(row["status"], "fully_funded")
        self.assertFalse(row["isPurchased"])
        self.assertIsNone(row["purchaseAmount"])
        self.assertEqual(row["assignedAmount"], "150000.00")
        self.assertEqual(row["reservedAmount"], "150000.00")
        self.assertEqual(row["releasedAmount"], "0.00")
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")

    def test_one_hundred_fifty_thousand_assignment_and_partial_purchase_release(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        response = self.spend(account, "145000.00", goal_id)
        self.assertEqual(response.status_code, 201, response.content)
        transaction = response.json()["transaction"]
        self.assertEqual(transaction["goalId"], goal_id)

        row = self.goal_row()
        self.assertEqual(row["status"], "purchased")
        self.assertTrue(row["isPurchased"])
        self.assertEqual(row["targetAmount"], "150000.00")
        self.assertEqual(row["assignedAmount"], "150000.00")
        self.assertEqual(row["reservedAmount"], "0.00")
        self.assertEqual(row["consumedAmount"], "145000.00")
        self.assertEqual(row["releasedAmount"], "5000.00")
        self.assertEqual(row["purchaseAmount"], "145000.00")
        self.assertEqual(row["purchaseDate"], self.PURCHASE_DATE)
        self.assertEqual(row["purchaseTransactionId"], transaction["id"])

        summary = self.payload()["summary"]
        self.assertEqual(summary["totalAssigned"], "0.00")
        self.assertEqual(summary["totalConsumed"], "145000.00")
        self.assertEqual(summary["totalReleased"], "5000.00")
        self.assertEqual(summary["availableFunding"], "5000.00")

        assignment = GoalAssignment.objects.get(goal_id=goal_id)
        self.assertIsNotNone(assignment.released_at)
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("355000.00"))
        self.assertEqual(Transaction.objects.count(), 1)
        self.assert_funding_invariant()

    def test_purchase_equal_to_assignment_releases_nothing(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        response = self.spend(account, "150000.00", goal_id)
        self.assertEqual(response.status_code, 201, response.content)
        row = self.goal_row()
        self.assertEqual(row["status"], "purchased")
        self.assertEqual(row["purchaseAmount"], "150000.00")
        self.assertEqual(row["consumedAmount"], "150000.00")
        self.assertEqual(row["releasedAmount"], "0.00")
        summary = self.payload()["summary"]
        self.assertEqual(summary["availableFunding"], "0.00")
        self.assertEqual(summary["totalConsumed"], "150000.00")
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("350000.00"))
        self.assert_funding_invariant()

    def test_purchase_above_assignment_never_releases_negative_funding(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        response = self.spend(account, "170000.00", goal_id)
        self.assertEqual(response.status_code, 201, response.content)
        row = self.goal_row()
        self.assertEqual(row["status"], "purchased")
        self.assertEqual(row["purchaseAmount"], "170000.00")
        self.assertEqual(row["assignedAmount"], "150000.00")
        self.assertEqual(row["consumedAmount"], "150000.00")
        self.assertEqual(row["releasedAmount"], "0.00")
        summary = self.payload()["summary"]
        self.assertEqual(summary["availableFunding"], "0.00")
        self.assertEqual(summary["totalConsumed"], "150000.00")
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("330000.00"))
        self.assert_funding_invariant()

    def test_new_allocation_accumulates_with_unused_purchase_funding(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        self.assertEqual(
            self.spend(account, "145000.00", goal_id).status_code, 201
        )
        self.assertEqual(self.payload()["summary"]["availableFunding"], "5000.00")
        self.add_allocation("2026-12", "50000.00")
        summary = self.payload()["summary"]
        self.assertEqual(summary["totalFunding"], "200000.00")
        self.assertEqual(summary["totalConsumed"], "145000.00")
        self.assertEqual(summary["availableFunding"], "55000.00")
        self.assert_funding_invariant()

    def test_funding_invariant_holds_across_purchase_reversal_lifecycle(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        self.assert_funding_invariant()
        created = self.spend(
            account, "145000.00", goal_id
        ).json()["transaction"]
        self.assert_funding_invariant()
        self.add_allocation("2026-12", "50000.00")
        self.assert_funding_invariant()
        edited = self.edit_transaction(
            created["id"], account, "200000.00", goal_id
        )
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(self.goal_row()["consumedAmount"], "150000.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "50000.00")
        self.assert_funding_invariant()
        unlinked = self.edit_transaction(
            created["id"], account, "200000.00"
        )
        self.assertEqual(unlinked.status_code, 200, unlinked.content)
        self.assertEqual(self.payload()["summary"]["totalAssigned"], "150000.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "50000.00")
        self.assert_funding_invariant()
        deleted = self.client.post(
            reverse("core:transaction_delete", args=[created["id"]])
        )
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertEqual(self.payload()["summary"]["totalAssigned"], "150000.00")
        self.assert_funding_invariant()

    def test_purchase_release_is_idempotent_for_repeated_requests(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        first = self.spend(
            account, "145000.00", goal_id, client_reference="purchase-1"
        )
        second = self.spend(
            account, "145000.00", goal_id, client_reference="purchase-1"
        )
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertTrue(second.json().get("duplicate"))
        self.assertEqual(Transaction.objects.count(), 1)
        assignment = GoalAssignment.objects.get(goal_id=goal_id)
        self.assertIsNotNone(assignment.released_at)
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("355000.00"))
        self.assertEqual(self.payload()["summary"]["availableFunding"], "5000.00")

    def test_second_expense_cannot_link_already_purchased_goal(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        self.assertEqual(
            self.spend(account, "145000.00", goal_id).status_code, 201
        )
        duplicate = self.spend(account, "5000.00", goal_id)
        self.assertEqual(duplicate.status_code, 400, duplicate.content)
        self.assertIn("goal", duplicate.json()["errors"])
        self.assertEqual(Transaction.objects.count(), 1)
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("355000.00"))
        self.assertEqual(self.payload()["summary"]["availableFunding"], "5000.00")

    def test_ordinary_expense_without_goal_link_is_unchanged(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        response = self.spend(account, "2000.00")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(response.json()["transaction"]["goalId"])
        row = self.goal_row()
        self.assertEqual(row["status"], "fully_funded")
        self.assertFalse(row["isPurchased"])
        self.assertEqual(row["releasedAmount"], "0.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")
        assignment = GoalAssignment.objects.get(goal_id=goal_id)
        self.assertIsNone(assignment.released_at)
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("498000.00"))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_editing_linked_expense_does_not_release_funding_twice(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        created = self.spend(account, "145000.00", goal_id).json()["transaction"]
        released_at = GoalAssignment.objects.get(goal_id=goal_id).released_at
        edited = self.edit_transaction(
            created["id"], account, "140000.00", goal_id
        )
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(edited.json()["transaction"]["amount"], "140000.00")
        self.assertEqual(Transaction.objects.count(), 1)
        assignment = GoalAssignment.objects.get(goal_id=goal_id)
        self.assertEqual(assignment.released_at, released_at)
        row = self.goal_row()
        self.assertEqual(row["status"], "purchased")
        self.assertEqual(row["purchaseAmount"], "140000.00")
        self.assertEqual(row["consumedAmount"], "140000.00")
        self.assertEqual(row["releasedAmount"], "10000.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "10000.00")
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("360000.00"))

    def test_reassigned_released_funding_is_not_available_twice(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        self.add_allocation("2026-11", "15000.00")
        goal_a = self.goal_id(name="Goal A", target="15000.00")
        self.assign(goal_a, "15000.00")
        created = self.spend(
            account, "14500.00", goal_a
        ).json()["transaction"]
        self.assertEqual(self.payload()["summary"]["availableFunding"], "500.00")

        goal_b = self.goal_id(name="Goal B", target="500.00")
        assigned = self.assign(goal_b, "500.00")
        self.assertEqual(assigned.status_code, 201, assigned.content)
        summary = self.payload()["summary"]
        self.assertEqual(summary["totalFunding"], "15000.00")
        self.assertEqual(summary["totalAssigned"], "500.00")
        self.assertEqual(summary["totalConsumed"], "14500.00")
        self.assertEqual(summary["availableFunding"], "0.00")
        self.assertEqual(
            Decimal(summary["totalFunding"]),
            Decimal(summary["availableFunding"])
            + Decimal(summary["totalAssigned"])
            + Decimal(summary["totalConsumed"]),
        )

        refused = self.client.post(
            reverse("core:transaction_delete", args=[created["id"]])
        )
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertEqual(
            refused.json().get("code"), "goal_funding_reassigned"
        )
        self.assertEqual(
            refused.json()["errors"]["__all__"],
            [goals_calc.BLOCKED_PURCHASE_REMOVAL_MESSAGE],
        )
        self.assertTrue(Transaction.objects.filter(pk=created["id"]).exists())
        self.assertEqual(self.goal_row("Goal A")["status"], "purchased")
        self.assertEqual(self.goal_row("Goal B")["status"], "fully_funded")
        self.assertIsNone(
            GoalAssignment.objects.get(goal_id=goal_b).released_at
        )
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("485500.00"))

        goal_b_assignment = GoalAssignment.objects.get(goal_id=goal_b)
        reversed_response = self.reverse_assignment(
            goal_b, goal_b_assignment.pk
        )
        self.assertEqual(
            reversed_response.status_code, 200, reversed_response.content
        )
        self.assertEqual(self.payload()["summary"]["availableFunding"], "500.00")

        deleted = self.client.post(
            reverse("core:transaction_delete", args=[created["id"]])
        )
        self.assertEqual(deleted.status_code, 200, deleted.content)
        row = self.goal_row("Goal A")
        self.assertEqual(row["status"], "fully_funded")
        self.assertEqual(row["reservedAmount"], "15000.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")
        self.assertEqual(
            GoalAssignment.objects.filter(
                goal_id=goal_b, reversed_at__isnull=True
            ).count(),
            0,
        )
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("500000.00"))
        self.assert_funding_invariant()

    def test_unlinking_purchase_is_refused_when_released_funding_is_reassigned(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        self.add_allocation("2026-11", "15000.00")
        goal_a = self.goal_id(name="Goal A", target="15000.00")
        self.assign(goal_a, "15000.00")
        created = self.spend(
            account, "14500.00", goal_a
        ).json()["transaction"]
        goal_b = self.goal_id(name="Goal B", target="500.00")
        self.assign(goal_b, "500.00")

        refused = self.edit_transaction(
            created["id"], account, "14500.00"
        )
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertEqual(
            refused.json().get("code"), "goal_funding_reassigned"
        )
        self.assertEqual(
            refused.json()["errors"]["__all__"],
            [goals_calc.BLOCKED_PURCHASE_REMOVAL_MESSAGE],
        )
        entry = Transaction.objects.get(pk=created["id"])
        self.assertEqual(str(entry.goal_id), goal_a)
        self.assertEqual(self.goal_row("Goal A")["status"], "purchased")
        self.assertEqual(self.goal_row("Goal B")["reservedAmount"], "500.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("485500.00"))
        self.assert_funding_invariant()

        goal_b_assignment = GoalAssignment.objects.get(goal_id=goal_b)
        self.reverse_assignment(goal_b, goal_b_assignment.pk)
        unlinked = self.edit_transaction(
            created["id"], account, "14500.00"
        )
        self.assertEqual(unlinked.status_code, 200, unlinked.content)
        self.assertIsNone(Transaction.objects.get(pk=created["id"]).goal_id)
        self.assertEqual(self.goal_row("Goal A")["status"], "fully_funded")
        self.assertEqual(self.goal_row("Goal A")["reservedAmount"], "15000.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("485500.00"))
        self.assert_funding_invariant()

    def test_purchase_reversal_allowed_when_new_allocation_covers_reassigned_funding(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        self.add_allocation("2026-11", "15000.00")
        goal_a = self.goal_id(name="Goal A", target="15000.00")
        self.assign(goal_a, "15000.00")
        created = self.spend(
            account, "14500.00", goal_a
        ).json()["transaction"]
        goal_b = self.goal_id(name="Goal B", target="500.00")
        self.assign(goal_b, "500.00")
        self.add_allocation("2026-12", "500.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "500.00")

        deleted = self.client.post(
            reverse("core:transaction_delete", args=[created["id"]])
        )
        self.assertEqual(deleted.status_code, 200, deleted.content)
        row = self.goal_row("Goal A")
        self.assertEqual(row["status"], "fully_funded")
        self.assertEqual(row["reservedAmount"], "15000.00")
        self.assertEqual(self.goal_row("Goal B")["reservedAmount"], "500.00")
        summary = self.payload()["summary"]
        self.assertEqual(summary["totalAssigned"], "15500.00")
        self.assertEqual(summary["availableFunding"], "0.00")
        self.assertEqual(summary["totalConsumed"], "0.00")
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("500000.00"))
        self.assert_funding_invariant()

    def test_purchase_reversal_refused_when_only_part_of_reassigned_funding_is_available(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        self.add_allocation("2026-11", "15000.00")
        goal_a = self.goal_id(name="Goal A", target="15000.00")
        self.assign(goal_a, "15000.00")
        created = self.spend(
            account, "14500.00", goal_a
        ).json()["transaction"]
        goal_b = self.goal_id(name="Goal B", target="500.00")
        self.assign(goal_b, "500.00")
        self.add_allocation("2026-12", "200.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "200.00")

        refused = self.client.post(
            reverse("core:transaction_delete", args=[created["id"]])
        )
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertTrue(Transaction.objects.filter(pk=created["id"]).exists())
        self.assertEqual(self.goal_row("Goal A")["status"], "purchased")
        self.assertEqual(self.goal_row("Goal B")["reservedAmount"], "500.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "200.00")
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("485500.00"))
        self.assert_funding_invariant()

    def test_increasing_purchase_is_refused_when_released_funding_is_reassigned(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        self.add_allocation("2026-11", "150000.00")
        laptop_id = self.goal_id(name="Laptop", target="150000.00")
        self.assign(laptop_id, "150000.00")
        created = self.spend(
            account, "145000.00", laptop_id
        ).json()["transaction"]
        phone_id = self.goal_id(name="Phone", target="5000.00")
        self.assign(phone_id, "5000.00")

        refused = self.edit_transaction(
            created["id"], account, "150000.00", laptop_id
        )
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertEqual(
            refused.json().get("code"), "goal_funding_exceeded"
        )
        self.assertEqual(
            refused.json()["errors"]["amount"],
            [goals_calc.FUNDING_EXCEEDED_MESSAGE],
        )
        self.assertEqual(self.goal_row()["consumedAmount"], "145000.00")
        self.assertEqual(self.goal_row()["releasedAmount"], "5000.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")
        self.assertEqual(
            Transaction.objects.get(pk=created["id"]).amount,
            Decimal("145000.00"),
        )
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("355000.00"))
        self.assert_funding_invariant()

        phone_assignment = GoalAssignment.objects.get(
            goal_id=phone_id, reversed_at__isnull=True
        )
        self.reverse_assignment(phone_id, phone_assignment.pk)
        accepted = self.edit_transaction(
            created["id"], account, "150000.00", laptop_id
        )
        self.assertEqual(accepted.status_code, 200, accepted.content)
        row = self.goal_row()
        self.assertEqual(row["consumedAmount"], "150000.00")
        self.assertEqual(row["releasedAmount"], "0.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("350000.00"))
        self.assert_funding_invariant()

    def test_unlinking_expense_restores_assignment_and_keeps_expense(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        created = self.spend(account, "145000.00", goal_id).json()["transaction"]
        unlinked = self.edit_transaction(created["id"], account, "145000.00")
        self.assertEqual(unlinked.status_code, 200, unlinked.content)
        self.assertIsNone(unlinked.json()["transaction"]["goalId"])
        self.assertTrue(Transaction.objects.filter(pk=created["id"]).exists())
        assignment = GoalAssignment.objects.get(goal_id=goal_id)
        self.assertIsNone(assignment.released_at)
        row = self.goal_row()
        self.assertEqual(row["status"], "fully_funded")
        self.assertFalse(row["isPurchased"])
        self.assertEqual(row["releasedAmount"], "0.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("355000.00"))

    def test_moving_purchase_to_another_goal_restores_and_releases_correctly(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        self.add_allocation("2026-11", "300000.00")
        laptop_id = self.goal_id(name="Laptop", target="150000.00")
        phone_id = self.goal_id(name="Phone", target="150000.00")
        self.assign(laptop_id, "150000.00")
        self.assign(phone_id, "150000.00")
        created = self.spend(
            account, "145000.00", laptop_id
        ).json()["transaction"]
        moved = self.edit_transaction(
            created["id"], account, "145000.00", phone_id
        )
        self.assertEqual(moved.status_code, 200, moved.content)
        self.assertEqual(moved.json()["transaction"]["goalId"], phone_id)
        rows = {row["name"]: row for row in self.payload()["goals"]}
        self.assertEqual(rows["Laptop"]["status"], "fully_funded")
        self.assertEqual(rows["Laptop"]["releasedAmount"], "0.00")
        self.assertEqual(rows["Phone"]["status"], "purchased")
        self.assertEqual(rows["Phone"]["consumedAmount"], "145000.00")
        self.assertEqual(rows["Phone"]["releasedAmount"], "5000.00")
        self.assertEqual(self.payload()["summary"]["availableFunding"], "5000.00")
        self.assertEqual(
            GoalAssignment.objects.filter(released_at__isnull=False).count(), 1
        )

    def test_deleting_linked_expense_restores_funding_and_balance(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        created = self.spend(account, "145000.00", goal_id).json()["transaction"]
        deleted = self.client.post(
            reverse("core:transaction_delete", args=[created["id"]])
        )
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertFalse(Transaction.objects.filter(pk=created["id"]).exists())
        assignment = GoalAssignment.objects.get(goal_id=goal_id)
        self.assertIsNone(assignment.released_at)
        row = self.goal_row()
        self.assertEqual(row["status"], "fully_funded")
        self.assertFalse(row["isPurchased"])
        self.assertEqual(self.payload()["summary"]["availableFunding"], "0.00")
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("500000.00"))

    def test_delete_is_refused_when_released_funding_is_reassigned(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        self.add_allocation("2026-11", "150000.00")
        laptop_id = self.goal_id(name="Laptop", target="150000.00")
        self.assign(laptop_id, "150000.00")
        created = self.spend(
            account, "145000.00", laptop_id
        ).json()["transaction"]
        phone_id = self.goal_id(name="Phone", target="150000.00")
        self.assign(phone_id, "5000.00")
        deleted = self.client.post(
            reverse("core:transaction_delete", args=[created["id"]])
        )
        self.assertEqual(deleted.status_code, 409, deleted.content)
        self.assertEqual(
            deleted.json().get("code"), "goal_funding_reassigned"
        )
        self.assertEqual(
            deleted.json()["errors"]["__all__"],
            [goals_calc.BLOCKED_PURCHASE_REMOVAL_MESSAGE],
        )
        self.assertTrue(Transaction.objects.filter(pk=created["id"]).exists())
        self.assertEqual(self.goal_row("Laptop")["status"], "purchased")
        self.assertEqual(self.goal_row("Phone")["status"], "active")
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("355000.00"))

    def test_released_assignment_cannot_be_reversed_separately(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        self.spend(account, "145000.00", goal_id)
        assignment = GoalAssignment.objects.get(goal_id=goal_id)
        response = self.reverse_assignment(goal_id, assignment.pk)
        self.assertEqual(response.status_code, 409, response.content)
        assignment.refresh_from_db()
        self.assertIsNone(assignment.reversed_at)
        self.assertIsNotNone(assignment.released_at)
        self.assertEqual(self.payload()["summary"]["availableFunding"], "5000.00")

    def test_purchased_goal_target_is_preserved(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        goal_id = self.funded_goal()
        self.spend(account, "145000.00", goal_id)
        response = self.post_json(
            reverse("core:goal_detail", args=[goal_id]),
            {
                "name": "Laptop",
                "targetAmount": "160000.00",
                "targetDate": "2026-12-31",
                "description": "",
            },
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("target_amount", response.json()["errors"])
        goal = Goal.objects.get(pk=goal_id)
        self.assertEqual(goal.target_amount, Decimal("150000.00"))
        self.assertEqual(self.goal_row()["purchaseAmount"], "145000.00")

    def test_income_and_transfer_cannot_link_goals(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        cash = self.create_account(
            name="Cash",
            account_type="cash",
            opening_balance=Decimal("0.00"),
        )
        goal_id = self.funded_goal()
        income = self.spend(
            account, "1000.00", goal_id, transaction_type="income"
        )
        self.assertEqual(income.status_code, 400, income.content)
        transfer = self.spend(
            account,
            "1000.00",
            goal_id,
            transaction_type="transfer",
            destination_account=str(cash.pk),
        )
        self.assertEqual(transfer.status_code, 400, transfer.content)
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(self.goal_row()["status"], "fully_funded")

    def test_cross_user_goal_cannot_be_linked(self):
        account = self.create_account(opening_balance=Decimal("500000.00"))
        other_goal = Goal.objects.create(
            owner=self.other_user,
            name="Their goal",
            target_amount=Decimal("150000.00"),
        )
        response = self.spend(account, "1000.00", other_goal.pk)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("goal", response.json()["errors"])
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Transaction.objects.filter(goal=other_goal).exists())


class GoalsSecurityTests(GoalsTestBase):
    def test_anonymous_users_are_redirected_to_login(self):
        anonymous = Client()
        response = anonymous.get(reverse("core:goals_api"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:login"), response.url)

    def test_other_users_cannot_read_or_modify_goals(self):
        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        assignment_id = self.assign(goal_id, "10000.00").json()["goals"][0][
            "assignments"
        ][0]["id"]
        other = Client()
        other.force_login(self.other_user)
        self.assertEqual(
            other.get(reverse("core:goal_detail", args=[goal_id])).status_code,
            404,
        )
        self.assertEqual(
            other.post(
                reverse("core:goal_detail", args=[goal_id]),
                data=json.dumps(
                    {
                        "name": "Hijack",
                        "targetAmount": "1.00",
                        "targetDate": None,
                    }
                ),
                content_type="application/json",
            ).status_code,
            404,
        )
        self.assertEqual(
            other.post(
                reverse("core:goal_assignments", args=[goal_id]),
                data=json.dumps({"amount": "100.00"}),
                content_type="application/json",
            ).status_code,
            404,
        )
        self.assertEqual(
            other.post(
                reverse(
                    "core:goal_assignment_reverse",
                    args=[goal_id, assignment_id],
                )
            ).status_code,
            404,
        )
        self.assertEqual(
            other.post(reverse("core:goal_delete", args=[goal_id])).status_code,
            404,
        )

    def test_other_users_goal_and_funding_are_isolated(self):
        self.add_allocation("2026-11", "50000.00")
        self.goal_id()
        other = Client()
        other.force_login(self.other_user)
        payload = other.get(reverse("core:goals_api")).json()
        self.assertEqual(payload["summary"]["availableFunding"], "0.00")
        self.assertEqual(payload["goals"], [])

    def test_unauthenticated_requests_are_rejected(self):
        response = Client().get(reverse("core:goals_api"))
        self.assertEqual(response.status_code, 302)
        response = Client().post(
            reverse("core:goal_create"),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)


class GoalsRegressionTests(GoalsTestBase):
    def test_account_balances_and_transactions_are_unchanged(self):
        account = self.create_account(opening_balance=Decimal("1000.00"))
        self.create_transaction(account, "income", "500.00")
        self.create_transaction(account, "expense", "200.00")
        before_balance = account.balance
        before_count = Transaction.objects.count()
        before_income = sum(
            Transaction.objects.filter(
                transaction_type=Transaction.TransactionType.INCOME
            ).values_list("amount", flat=True),
            Decimal("0.00"),
        )
        before_expense = sum(
            Transaction.objects.filter(
                transaction_type=Transaction.TransactionType.EXPENSE
            ).values_list("amount", flat=True),
            Decimal("0.00"),
        )

        self.add_allocation("2026-11", "50000.00")
        goal_id = self.goal_id()
        self.assign(goal_id, "30000.00")

        account.refresh_from_db()
        self.assertEqual(account.balance, before_balance)
        self.assertEqual(Transaction.objects.count(), before_count)
        self.assertEqual(
            sum(
                Transaction.objects.filter(
                    transaction_type=Transaction.TransactionType.INCOME
                ).values_list("amount", flat=True),
                Decimal("0.00"),
            ),
            before_income,
        )
        self.assertEqual(
            sum(
                Transaction.objects.filter(
                    transaction_type=Transaction.TransactionType.EXPENSE
                ).values_list("amount", flat=True),
                Decimal("0.00"),
            ),
            before_expense,
        )


    def test_budget_calculations_are_unchanged(self):
        allocation = self.add_allocation("2026-11", "50000.00")
        before = financials.summarize_month(self.user, allocation.budget.month)
        goal_id = self.goal_id()
        self.assign(goal_id, "30000.00")
        after = financials.summarize_month(self.user, allocation.budget.month)
        self.assertEqual(after["total_allocated"], before["total_allocated"])
        self.assertEqual(after["unallocated"], before["unallocated"])
        self.assertEqual(after["allocation_count"], before["allocation_count"])

    def test_savings_totals_are_unchanged(self):
        self.add_allocation("2026-11", "40000.00", category_name="Savings")
        before = savings_calc.total_saved(self.user)
        self.add_allocation("2026-11", "50000.00", category_name="Goals")
        goal_id = self.goal_id()
        self.assign(goal_id, "30000.00")
        self.assertEqual(savings_calc.total_saved(self.user), before)

    def test_goals_page_contains_required_server_ui(self):
        response = self.client.get(reverse("core:goals"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="gl-available-funding"')
        self.assertContains(response, 'id="gl-total-assigned"')
        self.assertContains(response, 'id="gl-goal-grid"')
        self.assertNotContains(response, "savings plan")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class GoalsConcurrencyConfigurationTests(TransactionTestCase):
    def test_sqlite_uses_immediate_write_transactions(self):
        from django.conf import settings

        options = settings.DATABASES["default"]["OPTIONS"]
        self.assertEqual(options["transaction_mode"], "IMMEDIATE")
        self.assertGreaterEqual(options["timeout"], 20)


class GoalsConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = self._create_user()
        self.add_allocation()
        self.goal = Goal.objects.create(
            owner=self.user,
            name="Laptop",
            target_amount=Decimal("150000.00"),
        )

    def _create_user(self):
        from .models import User

        return User.objects.create_user(
            email="concurrent@example.com", password="StrongPass123!"
        )

    def add_allocation(self):
        budget = MonthlyBudget.objects.create(
            owner=self.user, month=date(2026, 11, 1)
        )
        category = BudgetCategory.objects.create(owner=self.user, name="Goals")
        BudgetAllocation.objects.create(
            budget=budget,
            category=category,
            name="Goals",
            planned_amount=Decimal("50000.00"),
            status=BudgetAllocation.Status.ACTIVE,
        )

    def test_concurrent_assignments_never_exceed_available_funding(self):
        clients = []
        for _index in range(4):
            client = Client()
            client.force_login(self.user)
            clients.append(client)
        barrier = Barrier(len(clients))

        def assign(client):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = client.post(
                    reverse("core:goal_assignments", args=[self.goal.pk]),
                    data=json.dumps({"amount": "20000.00"}),
                    content_type="application/json",
                )
                return response.status_code
            except Exception:
                return 500
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            statuses = list(pool.map(assign, clients))

        assigned = sum(
            (
                item.amount
                for item in GoalAssignment.objects.filter(
                    owner=self.user, reversed_at__isnull=True
                )
            ),
            Decimal("0.00"),
        )
        self.assertLessEqual(assigned, Decimal("50000.00"))
        self.assertGreaterEqual(statuses.count(201), 1)
        self.assertLessEqual(statuses.count(201), 2)
