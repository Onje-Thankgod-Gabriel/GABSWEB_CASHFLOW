import json
from datetime import date
from decimal import Decimal

from django.test import Client, override_settings
from django.urls import reverse

from .models import (
    Account,
    BudgetAllocation,
    BudgetCategory,
    MonthlyBudget,
    Transaction,
)
from .test_accounts import AccountTestBase


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BudgetApiTests(AccountTestBase):
    def post_json(self, url, payload):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def create_income(self, amount="300000.00", **overrides):
        account = overrides.pop("account", None) or self.create_account()
        values = {
            "date": date(2026, 11, 2),
            "description": "Salary",
        }
        values.update(overrides)
        return self.create_transaction(account, "income", amount, **values)

    def create_allocation(self, category="Feeding", amount="30000.00", month="2026-11", **overrides):
        self.client.force_login(self.user)
        payload = {
            "month": month,
            "category": category,
            "name": overrides.pop("name", category),
            "planned_amount": amount,
            "status": overrides.pop("status", "active"),
            "description": overrides.pop("description", ""),
        }
        payload.update(overrides)
        response = self.post_json(reverse("core:budget_allocations"), payload)
        self.assertEqual(response.status_code, 201, response.content)
        return BudgetAllocation.objects.get(pk=response.json()["allocation"]["id"])

    def test_authentication_is_required(self):
        response = self.client.get(reverse("core:budget_api"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_empty_month_returns_zero_summary(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("core:budget_api"), {"month": "2026-11"}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["selected"]["month"], "2026-11")
        self.assertEqual(payload["selected"]["monthlyIncome"], "0.00")
        self.assertEqual(payload["selected"]["totalAllocated"], "0.00")
        self.assertEqual(payload["selected"]["unallocatedFunds"], "0.00")
        self.assertEqual(payload["selected"]["overAllocatedFunds"], "0.00")
        self.assertEqual(payload["allocations"], [])
        self.assertEqual(payload["categories"], [])

    def test_category_created_and_reused_without_duplicates(self):
        self.create_allocation(category="Feeding")
        self.create_allocation(category="feeding", name="Feeding again")
        self.assertEqual(
            BudgetCategory.objects.filter(owner=self.user).count(), 1
        )
        response = self.client.get(reverse("core:budget_categories"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["categories"]), 1)

    def test_custom_category_is_allowed(self):
        allocation = self.create_allocation(category="Church offering")
        self.assertEqual(allocation.category.name, "Church offering")

    def test_category_can_be_renamed_and_reused(self):
        allocation = self.create_allocation(category="Feeding")
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:budget_category_detail", args=[allocation.category_id]),
            {"name": "Food and groceries"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        allocation.refresh_from_db()
        self.assertEqual(allocation.category.name, "Food and groceries")
        second = self.create_allocation(category="food and groceries")
        self.assertEqual(second.category_id, allocation.category_id)

    def test_category_delete_is_blocked_while_used(self):
        allocation = self.create_allocation(category="Feeding")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:budget_category_delete", args=[allocation.category_id])
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(
            BudgetCategory.objects.filter(pk=allocation.category_id).exists()
        )

    def test_unused_category_can_be_deleted(self):
        category = BudgetCategory.objects.create(owner=self.user, name="Unused")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:budget_category_delete", args=[category.pk])
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(BudgetCategory.objects.filter(pk=category.pk).exists())

    def test_cross_user_category_access_is_blocked(self):
        category = BudgetCategory.objects.create(
            owner=self.other_user, name="Theirs"
        )
        self.client.force_login(self.user)
        edit = self.post_json(
            reverse("core:budget_category_detail", args=[category.pk]),
            {"name": "Renamed"},
        )
        self.assertEqual(edit.status_code, 404)
        delete = self.client.post(
            reverse("core:budget_category_delete", args=[category.pk])
        )
        self.assertEqual(delete.status_code, 404)

    def test_monthly_income_and_unallocated_calculation(self):
        self.create_income("300000.00")
        self.create_allocation(category="Feeding", amount="30000.00")
        self.create_allocation(category="Transport", amount="20000.00")
        response = self.client.get(
            reverse("core:budget_api"), {"month": "2026-11"}
        )
        selected = response.json()["selected"]
        self.assertEqual(selected["monthlyIncome"], "300000.00")
        self.assertEqual(selected["totalAllocated"], "50000.00")
        self.assertEqual(selected["unallocatedFunds"], "250000.00")
        self.assertEqual(selected["allocationCount"], 2)

    def selected_summary(self, month="2026-11"):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:budget_api"), {"month": month})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["selected"]

    def test_unallocated_never_negative_when_income_exceeds_allocations(self):
        self.create_income("300000.00")
        self.create_allocation(category="Feeding", amount="80000.00")
        selected = self.selected_summary()
        self.assertEqual(selected["monthlyIncome"], "300000.00")
        self.assertEqual(selected["totalAllocated"], "80000.00")
        self.assertEqual(selected["unallocatedFunds"], "220000.00")
        self.assertEqual(selected["overAllocatedFunds"], "0.00")

    def test_exactly_allocated_income_leaves_zero_unallocated(self):
        self.create_income("300000.00")
        self.create_allocation(category="Feeding", amount="180000.00")
        self.create_allocation(category="Transport", amount="120000.00")
        selected = self.selected_summary()
        self.assertEqual(selected["totalAllocated"], "300000.00")
        self.assertEqual(selected["unallocatedFunds"], "0.00")
        self.assertEqual(selected["overAllocatedFunds"], "0.00")

    def test_over_allocated_income_is_reported_separately_and_never_negative(self):
        self.create_income("300000.00")
        self.create_allocation(category="Feeding", amount="200000.00")
        self.create_allocation(category="Transport", amount="150000.00")
        selected = self.selected_summary()
        self.assertEqual(selected["totalAllocated"], "350000.00")
        self.assertEqual(selected["unallocatedFunds"], "0.00")
        self.assertNotIn("-", selected["unallocatedFunds"])
        self.assertEqual(selected["overAllocatedFunds"], "50000.00")

    def test_allocations_without_income_are_over_allocated_not_negative(self):
        self.create_allocation(category="Feeding", amount="50000.00")
        selected = self.selected_summary()
        self.assertEqual(selected["monthlyIncome"], "0.00")
        self.assertEqual(selected["unallocatedFunds"], "0.00")
        self.assertEqual(selected["overAllocatedFunds"], "50000.00")

    def test_editing_allocation_updates_month_summary(self):
        self.create_income("300000.00")
        allocation = self.create_allocation(category="Feeding", amount="80000.00")
        selected = self.selected_summary()
        self.assertEqual(selected["unallocatedFunds"], "220000.00")
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:budget_allocation_detail", args=[allocation.pk]),
            {
                "month": "2026-11",
                "category": "Feeding",
                "name": "Feeding",
                "planned_amount": "100000.00",
                "status": "active",
                "description": "",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        selected = self.selected_summary()
        self.assertEqual(selected["totalAllocated"], "100000.00")
        self.assertEqual(selected["unallocatedFunds"], "200000.00")

    def test_deleting_allocation_updates_month_summary(self):
        self.create_income("300000.00")
        allocation = self.create_allocation(category="Feeding", amount="80000.00")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:budget_allocation_delete", args=[allocation.pk])
        )
        self.assertEqual(response.status_code, 200, response.content)
        selected = self.selected_summary()
        self.assertEqual(selected["totalAllocated"], "0.00")
        self.assertEqual(selected["unallocatedFunds"], "300000.00")

    def test_income_change_recalculates_unallocated(self):
        self.create_income("300000.00")
        self.create_allocation(category="Feeding", amount="80000.00")
        self.assertEqual(self.selected_summary()["unallocatedFunds"], "220000.00")
        account = Account.objects.filter(owner=self.user).first()
        self.create_transaction(
            account, "income", "50000.00", date=date(2026, 11, 20)
        )
        selected = self.selected_summary()
        self.assertEqual(selected["monthlyIncome"], "350000.00")
        self.assertEqual(selected["unallocatedFunds"], "270000.00")

    def test_expense_does_not_change_planned_allocation_total(self):
        account = self.create_account()
        allocation = self.create_allocation(category="Feeding", amount="30000.00")
        entry = self.create_transaction(
            account, "expense", "5000.00", date=date(2026, 11, 3)
        )
        entry.allocation = allocation
        entry.save()
        selected = self.selected_summary()
        self.assertEqual(selected["totalAllocated"], "30000.00")
        self.assertEqual(selected["totalActualSpending"], "5000.00")
        self.assertEqual(selected["allocations"][0]["remainingAmount"], "25000.00")
        self.assertEqual(selected["allocations"][0]["overSpent"], "0.00")

    def test_per_category_overspending_is_reported(self):
        account = self.create_account()
        allocation = self.create_allocation(category="Feeding", amount="5000.00")
        entry = self.create_transaction(
            account, "expense", "7500.00", date=date(2026, 11, 3)
        )
        entry.allocation = allocation
        entry.save()
        row = self.selected_summary()["allocations"][0]
        self.assertEqual(row["remainingAmount"], "-2500.00")
        self.assertEqual(row["overSpent"], "2500.00")

    def test_monthly_budgets_are_independent(self):
        self.create_income("300000.00", date=date(2026, 11, 2))
        self.create_income("200000.00", date=date(2026, 12, 2))
        self.create_allocation(category="Feeding", amount="30000.00", month="2026-11")
        self.create_allocation(category="Feeding", amount="50000.00", month="2026-12")
        response = self.client.get(
            reverse("core:budget_api"), {"month": "2026-12"}
        )
        payload = response.json()
        self.assertEqual(payload["selected"]["month"], "2026-12")
        self.assertEqual(payload["selected"]["monthlyIncome"], "200000.00")
        self.assertEqual(payload["selected"]["totalAllocated"], "50000.00")
        months = {entry["month"]: entry for entry in payload["months"]}
        self.assertEqual(months["2026-11"]["totalAllocated"], "30000.00")
        self.assertEqual(months["2026-11"]["monthlyIncome"], "300000.00")

    def test_allocation_edit_does_not_create_transaction_or_change_balance(self):
        account = self.create_account(opening_balance=Decimal("1000.00"))
        allocation = self.create_allocation(amount="300.00")
        self.assertEqual(Transaction.objects.count(), 0)
        balance_before = account.balance
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:budget_allocation_detail", args=[allocation.pk]),
            {
                "month": "2026-11",
                "category": "Feeding",
                "name": "Feeding",
                "planned_amount": "450.00",
                "status": "active",
                "description": "Updated plan",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        allocation.refresh_from_db()
        account.refresh_from_db()
        self.assertEqual(allocation.planned_amount, Decimal("450.00"))
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(account.balance, balance_before)

    def test_linked_expense_reduces_remaining_amount_only(self):
        account = self.create_account(opening_balance=Decimal("1000.00"))
        allocation = self.create_allocation(category="Feeding", amount="30000.00")
        self.create_income("300000.00", account=account)
        entry = self.create_transaction(
            account, "expense", "5000.00", date=date(2026, 11, 3)
        )
        self.client.force_login(self.user)
        link = self.post_json(
            reverse("core:transaction_allocation", args=[entry.pk]),
            {"allocationId": str(allocation.pk)},
        )
        self.assertEqual(link.status_code, 200, link.content)
        allocation.refresh_from_db()
        self.assertEqual(allocation.actual_spending, Decimal("5000.00"))
        self.assertEqual(allocation.remaining_amount, Decimal("25000.00"))
        self.assertEqual(account.balance, Decimal("1000.00") + Decimal("300000.00") - Decimal("5000.00"))
        response = self.client.get(
            reverse("core:budget_api"), {"month": "2026-11"}
        )
        selected = response.json()["selected"]
        self.assertEqual(selected["totalActualSpending"], "5000.00")
        self.assertEqual(selected["totalRemaining"], "25000.00")
        self.assertEqual(selected["allocations"][0]["remainingAmount"], "25000.00")

    def test_multiple_expenses_accumulate(self):
        account = self.create_account()
        allocation = self.create_allocation(amount="30000.00")
        first = self.create_transaction(
            account, "expense", "5000.00", date=date(2026, 11, 3)
        )
        second = self.create_transaction(
            account, "expense", "3000.00", date=date(2026, 11, 4)
        )
        self.client.force_login(self.user)
        for entry in (first, second):
            self.post_json(
                reverse("core:transaction_allocation", args=[entry.pk]),
                {"allocationId": str(allocation.pk)},
            )
        self.assertEqual(allocation.actual_spending, Decimal("8000.00"))
        self.assertEqual(allocation.remaining_amount, Decimal("22000.00"))

    def test_expense_can_be_recorded_with_allocation_in_one_request(self):
        account = self.create_account()
        balance_before = account.balance
        allocation = self.create_allocation(category="Feeding", amount="30000.00")
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "expense",
                "amount": "5000.00",
                "date": "2026-11-03",
                "account": str(account.pk),
                "allocation": str(allocation.pk),
                "description": "Groceries",
                "clientReference": "budget-linked-expense-1",
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        entry = Transaction.objects.get(pk=response.json()["transaction"]["id"])
        self.assertEqual(entry.allocation_id, allocation.pk)
        self.assertEqual(allocation.actual_spending, Decimal("5000.00"))
        self.assertEqual(allocation.remaining_amount, Decimal("25000.00"))
        account.refresh_from_db()
        self.assertEqual(
            account.balance, balance_before - Decimal("5000.00")
        )

    def test_expense_can_exceed_an_exhausted_allocation(self):
        account = self.create_account()
        allocation = self.create_allocation(category="Feeding", amount="5000.00")
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "expense",
                "amount": "8000.00",
                "date": "2026-11-03",
                "account": str(account.pk),
                "allocation": str(allocation.pk),
                "description": "Unplanned but real expense",
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(allocation.actual_spending, Decimal("8000.00"))
        row = self.selected_summary()["allocations"][0]
        self.assertEqual(row["overSpent"], "3000.00")

    def test_expense_without_allocation_stays_unlinked(self):
        account = self.create_account()
        self.create_allocation(amount="30000.00")
        entry = self.create_transaction(
            account, "expense", "5000.00", date=date(2026, 11, 3)
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("core:budget_api"), {"month": "2026-11"}
        )
        selected = response.json()["selected"]
        self.assertEqual(selected["totalActualSpending"], "0.00")
        self.assertIsNone(entry.allocation_id)

    def test_income_and_transfer_are_excluded_from_allocation_spending(self):
        bank = self.create_account(name="Bank")
        cash = self.create_account(
            name="Cash", account_type=Account.AccountType.CASH
        )
        allocation = self.create_allocation(amount="30000.00")
        income = self.create_transaction(
            bank, "income", "9000.00", date=date(2026, 11, 3)
        )
        transfer = self.create_transaction(
            bank,
            "transfer",
            "4000.00",
            date=date(2026, 11, 4),
            destination_account=cash,
        )
        self.client.force_login(self.user)
        for entry in (income, transfer):
            response = self.post_json(
                reverse("core:transaction_allocation", args=[entry.pk]),
                {"allocationId": str(allocation.pk)},
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual(allocation.actual_spending, Decimal("0.00"))

    def test_overspending_is_reported_truthfully(self):
        account = self.create_account()
        allocation = self.create_allocation(amount="5000.00")
        entry = self.create_transaction(
            account, "expense", "7500.00", date=date(2026, 11, 3)
        )
        self.client.force_login(self.user)
        self.post_json(
            reverse("core:transaction_allocation", args=[entry.pk]),
            {"allocationId": str(allocation.pk)},
        )
        self.assertEqual(allocation.remaining_amount, Decimal("-2500.00"))
        response = self.client.get(
            reverse("core:budget_api"), {"month": "2026-11"}
        )
        payload = response.json()["selected"]
        self.assertEqual(payload["totalRemaining"], "-2500.00")
        self.assertEqual(payload["allocations"][0]["remainingAmount"], "-2500.00")

    def test_editing_linked_expense_recalculates_spending(self):
        account = self.create_account()
        allocation = self.create_allocation(amount="30000.00")
        entry = self.create_transaction(
            account, "expense", "5000.00", date=date(2026, 11, 3)
        )
        self.client.force_login(self.user)
        self.post_json(
            reverse("core:transaction_allocation", args=[entry.pk]),
            {"allocationId": str(allocation.pk)},
        )
        edit = self.post_json(
            reverse("core:transaction_detail", args=[entry.pk]),
            {
                "transaction_type": "expense",
                "amount": "2000.00",
                "date": "2026-11-03",
                "account": str(account.pk),
                "allocation": str(allocation.pk),
                "description": "Adjusted",
            },
        )
        self.assertEqual(edit.status_code, 200, edit.content)
        self.assertEqual(allocation.actual_spending, Decimal("2000.00"))

    def test_deleting_linked_expense_recalculates_spending(self):
        account = self.create_account()
        allocation = self.create_allocation(amount="30000.00")
        entry = self.create_transaction(
            account, "expense", "5000.00", date=date(2026, 11, 3)
        )
        self.client.force_login(self.user)
        self.post_json(
            reverse("core:transaction_allocation", args=[entry.pk]),
            {"allocationId": str(allocation.pk)},
        )
        response = self.client.post(
            reverse("core:transaction_delete", args=[entry.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(allocation.actual_spending, Decimal("0.00"))

    def test_cross_month_expense_cannot_be_linked(self):
        account = self.create_account()
        allocation = self.create_allocation(month="2026-11")
        entry = self.create_transaction(
            account, "expense", "1000.00", date=date(2026, 12, 2)
        )
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:transaction_allocation", args=[entry.pk]),
            {"allocationId": str(allocation.pk)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(entry.allocation_id)

    def test_cross_user_access_is_blocked(self):
        self.create_allocation(category="Mine")
        other_allocation = self.create_allocation(
            category="Theirs", month="2026-12"
        )
        other_allocation.budget.owner = self.other_user
        other_allocation.budget.save()
        other_allocation.category.owner = self.other_user
        other_allocation.category.save()
        self.client.force_login(self.user)
        detail = self.client.get(
            reverse("core:budget_allocation_detail", args=[other_allocation.pk])
        )
        self.assertEqual(detail.status_code, 404)
        edit = self.post_json(
            reverse("core:budget_allocation_detail", args=[other_allocation.pk]),
            {
                "month": "2026-12",
                "category": "Theirs",
                "planned_amount": "1.00",
                "status": "active",
            },
        )
        self.assertEqual(edit.status_code, 404)
        delete = self.client.post(
            reverse("core:budget_allocation_delete", args=[other_allocation.pk])
        )
        self.assertEqual(delete.status_code, 404)

    def test_cross_user_transaction_allocation_is_blocked(self):
        other_account = self.create_account(
            user=self.other_user, name="Theirs"
        )
        other_entry = self.create_transaction(
            other_account, "expense", "10.00", date=date(2026, 11, 3)
        )
        allocation = self.create_allocation()
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:transaction_allocation", args=[other_entry.pk]),
            {"allocationId": str(allocation.pk)},
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_create_expense_with_another_users_allocation(self):
        account = self.create_account()
        other_allocation = self.create_allocation(
            category="Theirs", month="2026-12"
        )
        other_allocation.budget.owner = self.other_user
        other_allocation.budget.save()
        other_allocation.category.owner = self.other_user
        other_allocation.category.save()
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "expense",
                "amount": "10.00",
                "date": "2026-12-03",
                "account": str(account.pk),
                "allocation": str(other_allocation.pk),
                "description": "Should be rejected",
            },
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(
            Transaction.objects.filter(owner=self.user).exists()
        )

    def test_allocation_with_linked_expense_cannot_be_deleted(self):
        account = self.create_account()
        allocation = self.create_allocation()
        entry = self.create_transaction(
            account, "expense", "100.00", date=date(2026, 11, 3)
        )
        entry.allocation = allocation
        entry.save()
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:budget_allocation_delete", args=[allocation.pk])
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(
            BudgetAllocation.objects.filter(pk=allocation.pk).exists()
        )

    def test_cancelled_allocation_is_kept_out_of_totals(self):
        self.create_income("100000.00")
        allocation = self.create_allocation(amount="20000.00")
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:budget_allocation_status", args=[allocation.pk]),
            {"status": "cancelled"},
        )
        self.assertEqual(response.status_code, 200)
        payload = self.client.get(
            reverse("core:budget_api"), {"month": "2026-11"}
        ).json()["selected"]
        self.assertEqual(payload["totalAllocated"], "0.00")
        self.assertEqual(payload["unallocatedFunds"], "100000.00")
        self.assertEqual(payload["allocationCount"], 0)
        self.assertTrue(
            BudgetAllocation.objects.filter(pk=allocation.pk).exists()
        )

    def test_decimal_precision_is_preserved(self):
        self.create_allocation(amount="12345.67")
        response = self.client.get(
            reverse("core:budget_api"), {"month": "2026-11"}
        )
        self.assertEqual(
            response.json()["selected"]["totalAllocated"], "12345.67"
        )

    def test_negative_and_zero_allocations_are_rejected(self):
        self.client.force_login(self.user)
        for amount in ("0.00", "-1.00"):
            response = self.post_json(
                reverse("core:budget_allocations"),
                {
                    "month": "2026-11",
                    "category": "Feeding",
                    "planned_amount": amount,
                    "status": "active",
                },
            )
            self.assertEqual(response.status_code, 400)
        self.assertFalse(BudgetAllocation.objects.exists())

    def test_csrf_is_required_for_writes(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(
            reverse("core:budget_allocations"),
            data=json.dumps(
                {
                    "month": "2026-11",
                    "category": "Feeding",
                    "planned_amount": "100.00",
                    "status": "active",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(BudgetAllocation.objects.exists())

    def test_duplicate_expense_submission_remains_idempotent(self):
        account = self.create_account()
        self.client.force_login(self.user)
        payload = {
            "transaction_type": "expense",
            "amount": "50.00",
            "date": "2026-11-03",
            "account": str(account.pk),
            "description": "Duplicate guard",
            "clientReference": "budget-duplicate-1",
        }
        first = self.post_json(reverse("core:transactions"), payload)
        second = self.post_json(reverse("core:transactions"), payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Transaction.objects.filter(owner=self.user).count(), 1)

    def test_budget_page_renders_for_authenticated_user(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:budgets"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "bd-allocation-grid")
        self.assertContains(response, "bd-csrf-token")
        self.assertContains(response, "bd-overallocation-banner")
        self.assertContains(response, "bd-categories-modal")
        self.assertContains(response, "Allocations are plans")

    def test_transaction_page_exposes_optional_allocation_field(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:transaction"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mf-tx-allocation")
        self.assertContains(response, "Budget allocation (optional)")
