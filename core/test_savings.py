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
    SavingsWithdrawal,
    Transaction,
)
from .test_accounts import AccountTestBase


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SavingsTests(AccountTestBase):
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
        category_name="Savings",
        status=BudgetAllocation.Status.ACTIVE,
        name=None,
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
            name=name or category_name,
            planned_amount=Decimal(amount),
            status=status,
        )

    def edit_allocation(self, allocation, **overrides):
        payload = {
            "month": allocation.budget.month_key,
            "category": allocation.category.name,
            "name": allocation.name,
            "planned_amount": str(allocation.planned_amount),
            "status": allocation.status,
            "description": allocation.description,
        }
        payload.update(overrides)
        return self.post_json(
            reverse("core:budget_allocation_detail", args=[allocation.pk]),
            payload,
        )

    def savings(self, month="2026-11"):
        response = self.client.get(reverse("core:savings_api"), {"month": month})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def withdraw(self, amount, day="2026-11-15", reason="Laptop repair", client_reference=None):
        payload = {"amount": amount, "date": day, "reason": reason}
        if client_reference:
            payload["clientReference"] = client_reference
        return self.post_json(reverse("core:savings_withdrawals"), payload)

    def test_november_budget_allocation_appears_as_saved(self):
        self.add_allocation("2026-11", "50000.00")
        payload = self.savings("2026-11")
        self.assertEqual(payload["summary"]["monthlySaved"], "50000.00")
        self.assertEqual(payload["summary"]["totalSaved"], "50000.00")
        self.assertEqual(
            next(row for row in payload["months"] if row["month"] == "2026-11")[
                "saved"
            ],
            "50000.00",
        )

    def test_december_allocation_appears_in_its_own_month(self):
        self.add_allocation("2026-11", "50000.00")
        self.add_allocation("2026-12", "30000.00")
        payload = self.savings("2026-12")
        self.assertEqual(payload["summary"]["monthlySaved"], "30000.00")
        november = next(row for row in payload["months"] if row["month"] == "2026-11")
        self.assertEqual(november["saved"], "50000.00")

    def test_cumulative_saved_sums_each_month_once(self):
        self.add_allocation("2026-11", "50000.00")
        self.add_allocation("2026-12", "30000.00")
        self.add_allocation("2027-01", "40000.00")
        payload = self.savings("2027-01")
        self.assertEqual(payload["summary"]["totalSaved"], "120000.00")
        self.assertEqual(payload["summary"]["monthlySaved"], "40000.00")

    def test_editing_allocation_updates_instead_of_adding(self):
        allocation = self.add_allocation("2026-11", "50000.00")
        response = self.edit_allocation(allocation, planned_amount="60000.00")
        self.assertEqual(response.status_code, 200, response.content)
        payload = self.savings("2026-11")
        self.assertEqual(payload["summary"]["monthlySaved"], "60000.00")
        self.assertEqual(payload["summary"]["totalSaved"], "60000.00")
        self.assertEqual(len(payload["months"]), 1)

    def test_removing_allocation_updates_savings(self):
        allocation = self.add_allocation("2026-11", "50000.00")
        self.add_allocation("2026-12", "30000.00")
        response = self.post_json(
            reverse("core:budget_allocation_delete", args=[allocation.pk])
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = self.savings("2026-11")
        self.assertEqual(payload["summary"]["monthlySaved"], "0.00")
        self.assertEqual(payload["summary"]["totalSaved"], "30000.00")

    def test_loading_savings_repeatedly_creates_no_records(self):
        self.add_allocation("2026-11", "50000.00")
        for _ in range(3):
            self.savings("2026-11")
        self.assertEqual(BudgetAllocation.objects.count(), 1)
        self.assertEqual(SavingsWithdrawal.objects.count(), 0)

    def test_month_without_allocation_shows_zero_saved(self):
        self.add_allocation("2026-11", "50000.00")
        payload = self.savings("2027-04")
        self.assertEqual(payload["summary"]["monthlySaved"], "0.00")
        self.assertEqual(payload["summary"]["monthlyWithdrawn"], "0.00")
        self.assertEqual(payload["summary"]["monthlyNet"], "0.00")

    def test_previous_months_remain_associated_with_their_allocations(self):
        self.add_allocation("2026-11", "50000.00")
        self.add_allocation("2026-12", "30000.00")
        payload = self.savings("2026-11")
        self.assertEqual(payload["month"], "2026-11")
        december = next(row for row in payload["months"] if row["month"] == "2026-12")
        self.assertEqual(december["saved"], "30000.00")

    def test_selected_month_summary_uses_its_budget_allocation(self):
        self.add_allocation("2026-11", "50000.00")
        self.add_allocation("2026-12", "30000.00")
        self.assertEqual(self.savings("2026-11")["summary"]["monthlySaved"], "50000.00")
        self.assertEqual(self.savings("2026-12")["summary"]["monthlySaved"], "30000.00")

    def test_allocation_is_not_counted_twice_as_a_contribution(self):
        self.add_allocation("2026-11", "50000.00")
        self.add_allocation("2026-12", "30000.00")
        payload = self.savings("2026-12")
        self.assertEqual(payload["summary"]["totalSaved"], "80000.00")
        self.assertEqual(SavingsWithdrawal.objects.count(), 0)
        self.assertEqual(
            sum(Decimal(row["saved"]) for row in payload["months"]), Decimal("80000.00")
        )

    def test_cancelled_allocation_does_not_count_as_saved(self):
        self.add_allocation(
            "2026-11",
            "50000.00",
            status=BudgetAllocation.Status.CANCELLED,
        )
        self.assertEqual(self.savings("2026-11")["summary"]["totalSaved"], "0.00")

    def test_invalid_month_is_rejected(self):
        response = self.client.get(reverse("core:savings_api"), {"month": "2026-13"})
        self.assertEqual(response.status_code, 400)

    def test_valid_withdrawal_is_recorded_with_reason_and_date(self):
        self.add_allocation("2026-11", "50000.00")
        response = self.withdraw("15000.00", reason="Laptop repair")
        self.assertEqual(response.status_code, 201, response.content)
        withdrawal = SavingsWithdrawal.objects.get(owner=self.user)
        self.assertEqual(withdrawal.amount, Decimal("15000.00"))
        self.assertEqual(withdrawal.reason, "Laptop repair")
        self.assertEqual(withdrawal.date, date(2026, 11, 15))
        payload = self.savings("2026-11")
        self.assertEqual(payload["withdrawals"][0]["reason"], "Laptop repair")

    def test_withdrawal_reduces_current_savings(self):
        self.add_allocation("2026-11", "120000.00")
        self.withdraw("15000.00")
        payload = self.savings("2026-11")
        self.assertEqual(payload["summary"]["totalSaved"], "120000.00")
        self.assertEqual(payload["summary"]["totalWithdrawn"], "15000.00")
        self.assertEqual(payload["summary"]["currentBalance"], "105000.00")

    def test_multiple_withdrawals_calculate_correctly(self):
        self.add_allocation("2026-11", "80000.00")
        self.withdraw("10000.00", "2026-11-02")
        self.withdraw("5000.00", "2026-11-20")
        payload = self.savings("2026-11")
        self.assertEqual(payload["summary"]["totalWithdrawn"], "15000.00")
        self.assertEqual(payload["summary"]["monthlyWithdrawn"], "15000.00")
        self.assertEqual(payload["summary"]["currentBalance"], "65000.00")

    def test_withdrawal_above_available_balance_is_rejected(self):
        self.add_allocation("2026-11", "50000.00")
        response = self.withdraw("50000.01")
        self.assertEqual(response.status_code, 400)
        self.assertIn("amount", response.json()["errors"])
        self.assertFalse(SavingsWithdrawal.objects.exists())

    def test_zero_negative_malformed_and_invalid_amounts_are_rejected(self):
        self.add_allocation("2026-11", "50000.00")
        cases = [
            ("0.00", "2026-11-15", "Reason"),
            ("-100.00", "2026-11-15", "Reason"),
            ("not-a-number", "2026-11-15", "Reason"),
            ("100.00", "2026-13-01", "Reason"),
            ("100.00", "2026-11-15", ""),
        ]
        for amount, day, reason in cases:
            response = self.withdraw(amount, day, reason)
            self.assertEqual(response.status_code, 400, (amount, day, response.content))
        self.assertFalse(SavingsWithdrawal.objects.exists())

    def test_withdrawal_cannot_make_savings_negative(self):
        self.add_allocation("2026-11", "1000.00")
        self.assertEqual(self.withdraw("1000.00").status_code, 201)
        self.assertEqual(self.withdraw("0.01").status_code, 400)
        self.assertEqual(
            self.savings("2026-11")["summary"]["currentBalance"], "0.00"
        )

    def test_delete_withdrawal_recalculates_balance(self):
        self.add_allocation("2026-11", "50000.00")
        response = self.withdraw("15000.00")
        withdrawal_id = response.json()["withdrawal"]["id"]
        delete = self.post_json(
            reverse("core:savings_withdrawal_delete", args=[withdrawal_id])
        )
        self.assertEqual(delete.status_code, 200, delete.content)
        self.assertEqual(
            self.savings("2026-11")["summary"]["currentBalance"], "50000.00"
        )

    def test_edit_withdrawal_recalculates_and_validates_balance(self):
        self.add_allocation("2026-11", "50000.00")
        response = self.withdraw("15000.00")
        withdrawal_id = response.json()["withdrawal"]["id"]
        edit = self.post_json(
            reverse("core:savings_withdrawal_detail", args=[withdrawal_id]),
            {
                "amount": "20000.00",
                "date": "2026-11-18",
                "reason": "Laptop repair and accessories",
            },
        )
        self.assertEqual(edit.status_code, 200, edit.content)
        payload = self.savings("2026-11")
        self.assertEqual(payload["summary"]["currentBalance"], "30000.00")
        self.assertEqual(payload["withdrawals"][0]["amount"], "20000.00")
        too_large = self.post_json(
            reverse("core:savings_withdrawal_detail", args=[withdrawal_id]),
            {
                "amount": "50000.01",
                "date": "2026-11-18",
                "reason": "Too much",
            },
        )
        self.assertEqual(too_large.status_code, 400)

    def test_edited_withdrawal_can_reuse_its_own_recorded_amount(self):
        self.add_allocation("2026-11", "100000.00")
        self.assertEqual(self.withdraw("20000.00", "2026-11-05").status_code, 201)
        second = self.withdraw("15000.00", "2026-11-15")
        self.assertEqual(second.status_code, 201, second.content)
        withdrawal_id = second.json()["withdrawal"]["id"]

        self.assertEqual(
            self.savings("2026-11")["summary"]["currentBalance"], "65000.00"
        )

        edited = self.post_json(
            reverse("core:savings_withdrawal_detail", args=[withdrawal_id]),
            {
                "amount": "30000.00",
                "date": "2026-11-18",
                "reason": "Laptop repair and accessories",
            },
        )
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(
            self.savings("2026-11")["summary"]["currentBalance"], "50000.00"
        )

        use_full_replacement = self.post_json(
            reverse("core:savings_withdrawal_detail", args=[withdrawal_id]),
            {
                "amount": "80000.00",
                "date": "2026-11-18",
                "reason": "Full replacement within available savings",
            },
        )
        self.assertEqual(
            use_full_replacement.status_code, 200, use_full_replacement.content
        )
        self.assertEqual(
            self.savings("2026-11")["summary"]["currentBalance"], "0.00"
        )

        overdraw = self.post_json(
            reverse("core:savings_withdrawal_detail", args=[withdrawal_id]),
            {
                "amount": "80000.01",
                "date": "2026-11-18",
                "reason": "Too much",
            },
        )
        self.assertEqual(overdraw.status_code, 400)
        self.assertIn("amount", overdraw.json()["errors"])
        self.assertEqual(
            SavingsWithdrawal.objects.get(pk=withdrawal_id).amount,
            Decimal("80000.00"),
        )
        self.assertEqual(
            self.savings("2026-11")["summary"]["currentBalance"], "0.00"
        )

    def test_new_withdrawal_available_excludes_all_previous_withdrawals(self):
        self.add_allocation("2026-11", "100000.00")
        self.assertEqual(self.withdraw("20000.00", "2026-11-05").status_code, 201)
        self.assertEqual(self.withdraw("15000.00", "2026-11-15").status_code, 201)
        summary = self.savings("2026-11")["summary"]
        self.assertEqual(summary["totalSaved"], "100000.00")
        self.assertEqual(summary["totalWithdrawn"], "35000.00")
        self.assertEqual(summary["currentBalance"], "65000.00")
        self.assertEqual(self.withdraw("65000.01").status_code, 400)
        self.assertEqual(self.withdraw("65000.00").status_code, 201)
        self.assertEqual(
            self.savings("2026-11")["summary"]["currentBalance"], "0.00"
        )

    def test_duplicate_withdrawal_submission_is_idempotent(self):
        self.add_allocation("2026-11", "50000.00")
        reference = "withdraw-once-1"
        first = self.withdraw("5000.00", client_reference=reference)
        second = self.withdraw("5000.00", client_reference=reference)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertIn(second.status_code, (200, 201))
        self.assertEqual(SavingsWithdrawal.objects.count(), 1)
        self.assertEqual(
            self.savings("2026-11")["summary"]["totalWithdrawn"], "5000.00"
        )

    def test_repeated_requests_cannot_oversell_savings(self):
        self.add_allocation("2026-11", "100000.00")
        self.assertEqual(self.withdraw("60000.00").status_code, 201)
        self.assertEqual(self.withdraw("60000.00").status_code, 400)
        self.assertEqual(
            self.savings("2026-11")["summary"]["currentBalance"], "40000.00"
        )

    def test_budget_allocation_creates_no_expense_income_or_transaction(self):
        account = self.create_account(opening_balance=Decimal("1000.00"))
        before = account.calculate_balance()
        self.add_allocation("2026-11", "50000.00")
        self.savings("2026-11")
        account.refresh_from_db()
        self.assertEqual(account.calculate_balance(), before)
        self.assertFalse(Transaction.objects.exists())

    def test_withdrawal_creates_no_transaction_or_account_change(self):
        account = self.create_account(opening_balance=Decimal("1000.00"))
        self.add_allocation("2026-11", "50000.00")
        before = account.calculate_balance()
        self.withdraw("15000.00")
        account.refresh_from_db()
        self.assertEqual(account.calculate_balance(), before)
        self.assertFalse(Transaction.objects.exists())

    def test_savings_does_not_change_other_budget_allocations(self):
        feeding = self.add_allocation(
            "2026-11", "40000.00", category_name="Feeding", name="Food"
        )
        self.add_allocation("2026-11", "50000.00")
        self.withdraw("10000.00")
        feeding.refresh_from_db()
        self.assertEqual(feeding.planned_amount, Decimal("40000.00"))
        self.assertEqual(feeding.status, BudgetAllocation.Status.ACTIVE)

    def test_budget_reduction_that_breaks_withdrawals_is_rejected(self):
        allocation = self.add_allocation("2026-11", "50000.00")
        self.withdraw("50000.00")
        response = self.edit_allocation(allocation, planned_amount="40000.00")
        self.assertEqual(response.status_code, 400)
        allocation.refresh_from_db()
        self.assertEqual(allocation.planned_amount, Decimal("50000.00"))

    def test_budget_cancellation_that_breaks_withdrawals_is_rejected(self):
        allocation = self.add_allocation("2026-11", "50000.00")
        self.withdraw("20000.00")
        response = self.post_json(
            reverse("core:budget_allocation_status", args=[allocation.pk]),
            {"status": "cancelled"},
        )
        self.assertEqual(response.status_code, 400)
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, BudgetAllocation.Status.ACTIVE)

    def test_budget_deletion_that_breaks_withdrawals_is_rejected(self):
        allocation = self.add_allocation("2026-11", "50000.00")
        self.withdraw("20000.00")
        response = self.post_json(
            reverse("core:budget_allocation_delete", args=[allocation.pk])
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(BudgetAllocation.objects.filter(pk=allocation.pk).exists())

    def test_safe_budget_edits_remain_allowed(self):
        allocation = self.add_allocation("2026-11", "50000.00")
        self.withdraw("20000.00")
        response = self.edit_allocation(allocation, planned_amount="25000.00")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self.savings("2026-11")["summary"]["currentBalance"], "5000.00"
        )
        allowed = self.edit_allocation(
            allocation, planned_amount="30000.00"
        )
        self.assertEqual(allowed.status_code, 200, allowed.content)

    def test_ownership_isolation_for_allocations_and_withdrawals(self):
        other_allocation = self.add_allocation(
            "2026-11", "90000.00", owner=self.other_user
        )
        other_withdrawal = SavingsWithdrawal.objects.create(
            owner=self.other_user,
            amount=Decimal("1000.00"),
            date=date(2026, 11, 3),
            reason="Other user",
        )
        self.add_allocation("2026-11", "50000.00")
        payload = self.savings("2026-11")
        self.assertEqual(payload["summary"]["totalSaved"], "50000.00")
        self.assertNotIn(
            str(other_withdrawal.pk),
            [item["id"] for item in payload["withdrawals"]],
        )
        delete = self.post_json(
            reverse("core:savings_withdrawal_delete", args=[other_withdrawal.pk])
        )
        self.assertEqual(delete.status_code, 404)
        edit = self.edit_allocation(other_allocation, planned_amount="1.00")
        self.assertEqual(edit.status_code, 404)

    def test_authentication_is_required(self):
        client = Client()
        get_response = client.get(reverse("core:savings_api"))
        self.assertEqual(get_response.status_code, 302)
        self.assertIn("/login/", get_response["Location"])
        post_response = client.post(reverse("core:savings_withdrawals"))
        self.assertEqual(post_response.status_code, 302)

    def test_csrf_is_required_for_withdrawals(self):
        self.add_allocation("2026-11", "50000.00")
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(
            reverse("core:savings_withdrawals"),
            data=json.dumps(
                {"amount": "100.00", "date": "2026-11-15", "reason": "Test"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SavingsWithdrawal.objects.exists())

    def test_data_persists_after_refresh_and_new_sign_in(self):
        self.add_allocation("2026-11", "50000.00")
        self.withdraw("15000.00")
        before = self.savings("2026-11")["summary"]
        self.client.logout()
        self.client.force_login(self.user)
        after = self.savings("2026-11")["summary"]
        self.assertEqual(before, after)

    def test_savings_page_has_no_plan_interface(self):
        response = self.client.get(reverse("core:savings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sv-withdraw-open")
        self.assertContains(response, "sv-month-select")
        self.assertContains(response, "sv-history-list")
        self.assertNotContains(response, "sv-new-plan")
        self.assertNotContains(response, "New savings plan")
        self.assertNotContains(response, "Target amount")
        self.assertNotContains(response, "Archived")

    def test_dashboard_savings_uses_authoritative_server_fields(self):
        response = self.client.get(reverse("core:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "db-savings-total")
        self.assertContains(response, "db-savings-balance")
        self.assertContains(response, "db-savings-month")
        self.assertNotContains(response, "db-savings-plans")
        self.assertNotContains(response, "Active plans")
        self.assertNotContains(response, "Set aside across savings plans")
        self.assertNotContains(response, "Apply Savings Plan")
        self.assertNotContains(response, "piggy-bank")

    def test_insights_savings_section_uses_server_history(self):
        response = self.client.get(reverse("core:insights"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Savings history")
        self.assertContains(response, "Savings withdrawals")
        self.assertContains(response, "ri-savings-history")
        self.assertNotContains(response, "ri-savings-plans")
        self.assertNotContains(response, "Savings plans")
        self.assertNotContains(response, "piggy-bank")
