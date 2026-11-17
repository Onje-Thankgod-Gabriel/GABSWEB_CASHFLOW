import re
from datetime import date
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse

from . import reports as reports_service
from .report_pdf import _chart, _date, _range_text
from .models import (
    Account,
    BudgetAllocation,
    BudgetCategory,
    Goal,
    GoalAssignment,
    MonthlyBudget,
    SavingsWithdrawal,
    Transaction,
)
from .test_accounts import AccountTestBase


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ReportsTests(AccountTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def add_allocation(
        self,
        month="2026-11",
        amount="50000.00",
        category_name="Savings",
        owner=None,
        status=BudgetAllocation.Status.ACTIVE,
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

    def api(self, mode="month", period="2026-11"):
        response = self.client.get(
            reverse("core:insights_api"), {"mode": mode, "period": period}
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["report"]

    def test_reports_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:insights"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:login"), response["Location"])

    def test_api_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:insights_api"))
        self.assertEqual(response.status_code, 302)

    def test_pdf_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:insights_pdf"))
        self.assertEqual(response.status_code, 302)

    def test_api_and_pdf_are_no_store(self):
        api = self.client.get(reverse("core:insights_api"))
        self.assertEqual(api.status_code, 200)
        self.assertIn("no-store", api["Cache-Control"])
        pdf = self.client.get(reverse("core:insights_pdf"))
        self.assertEqual(pdf.status_code, 200)
        self.assertIn("no-store", pdf["Cache-Control"])

    def test_invalid_period_is_rejected(self):
        for params in (
            {"mode": "week", "period": "2026-11"},
            {"mode": "month", "period": "2026-13"},
            {"mode": "month", "period": "9999-12"},
            {"mode": "month", "period": "0001-01"},
            {"mode": "year", "period": "not-a-year"},
            {"mode": "year", "period": "9999"},
        ):
            response = self.client.get(reverse("core:insights_api"), params)
            self.assertEqual(response.status_code, 400)
            pdf = self.client.get(reverse("core:insights_pdf"), params)
            self.assertEqual(pdf.status_code, 400)

    def test_report_scopes_to_authenticated_user(self):
        mine = self.create_account(name="Mine", opening_balance=Decimal("100.00"))
        theirs = self.create_account(
            owner=self.other_user,
            name="Theirs",
            opening_balance=Decimal("999.00"),
        )
        self.create_transaction(mine, "income", "200.00", date=date(2026, 11, 5))
        self.create_transaction(
            theirs, "income", "888.00", date=date(2026, 11, 5)
        )
        report = self.api()
        self.assertEqual(report["cash_flow"]["income"], "200.00")
        self.assertEqual(report["accounts"]["count"], 1)
        self.assertEqual(report["accounts"]["rows"][0]["name"], "Mine")

    def test_month_boundaries_do_not_leak_next_month(self):
        account = self.create_account(
            opening_balance=Decimal("0.00"),
            opening_balance_date=date(2026, 10, 1),
        )
        self.create_transaction(account, "income", "100.00", date=date(2026, 10, 31))
        self.create_transaction(account, "income", "300.00", date=date(2026, 11, 15))
        self.create_transaction(account, "expense", "40.00", date=date(2026, 11, 30))
        self.create_transaction(account, "income", "700.00", date=date(2026, 12, 1))
        report = self.api()
        self.assertEqual(report["cash_flow"]["income"], "300.00")
        self.assertEqual(report["cash_flow"]["expenses"], "40.00")
        self.assertEqual(report["cash_flow"]["net"], "260.00")
        self.assertEqual(report["balances"]["opening"], "100.00")
        self.assertEqual(report["balances"]["closing"], "360.00")

    def test_opening_balance_is_not_income(self):
        self.create_account(opening_balance=Decimal("1250.50"))
        report = self.api()
        self.assertEqual(report["cash_flow"]["income"], "0.00")
        self.assertEqual(report["balances"]["opening"], "1250.50")
        self.assertEqual(report["balances"]["closing"], "1250.50")

    def test_transfers_are_not_income_or_expenses(self):
        bank = self.create_account(name="Bank", opening_balance=Decimal("500.00"))
        cash = self.create_account(
            name="Cash",
            account_type=Account.AccountType.CASH,
            opening_balance=Decimal("0.00"),
        )
        self.create_transaction(
            bank,
            "transfer",
            "100.00",
            destination_account=cash,
            date=date(2026, 11, 3),
        )
        report = self.api()
        self.assertEqual(report["cash_flow"]["income"], "0.00")
        self.assertEqual(report["cash_flow"]["expenses"], "0.00")
        self.assertEqual(report["transfers"]["out"], "100.00")
        self.assertEqual(report["transfers"]["in"], "100.00")
        self.assertEqual(report["balances"]["closing"], "500.00")

    def test_budget_summary_reuses_authoritative_figures(self):
        account = self.create_account(
            opening_balance=Decimal("0.00"),
            opening_balance_date=date(2026, 11, 1),
        )
        self.create_transaction(
            account, "income", "100000.00", date=date(2026, 11, 1)
        )
        allocation = self.add_allocation("2026-11", "70000.00", "Rent")
        self.create_transaction(
            account,
            "expense",
            "25000.00",
            date=date(2026, 11, 4),
            allocation=allocation,
        )
        report = self.api()
        self.assertEqual(report["allocations"]["totalPlanned"], "70000.00")
        self.assertEqual(report["allocations"]["totalActual"], "25000.00")
        self.assertEqual(report["allocations"]["unallocated"], "30000.00")
        self.assertEqual(report["allocations"]["overAllocated"], "0.00")
        self.assertEqual(report["allocations"]["rows"][0]["remaining"], "45000.00")

    def test_over_allocation_is_reported_separately(self):
        self.add_allocation("2026-11", "80000.00", "Rent")
        report = self.api()
        self.assertEqual(report["allocations"]["unallocated"], "0.00")
        self.assertEqual(report["allocations"]["overAllocated"], "80000.00")

    def test_savings_activity_is_cumulative_to_period_end(self):
        self.add_allocation("2026-10", "40000.00", "Savings")
        self.add_allocation("2026-11", "30000.00", "Savings")
        self.add_allocation("2026-12", "90000.00", "Savings")
        SavingsWithdrawal.objects.create(
            owner=self.user,
            amount=Decimal("10000.00"),
            date=date(2026, 11, 10),
            reason="Repair",
        )
        SavingsWithdrawal.objects.create(
            owner=self.user,
            amount=Decimal("5000.00"),
            date=date(2026, 12, 2),
            reason="Later",
        )
        report = self.api()
        savings = report["savings"]
        self.assertEqual(savings["contributions"], "30000.00")
        self.assertEqual(savings["withdrawalTotal"], "10000.00")
        self.assertEqual(savings["cumulative_saved"], "70000.00")
        self.assertEqual(savings["cumulative_withdrawn"], "10000.00")
        self.assertEqual(savings["cumulative_balance"], "60000.00")
        self.assertEqual(len(savings["withdrawals"]), 1)

    def test_goals_funding_assignments_and_purchases(self):
        self.add_allocation("2026-11", "150000.00", "Goals")
        account = self.create_account(
            opening_balance=Decimal("0.00"),
            opening_balance_date=date(2026, 11, 1),
        )
        goal = Goal.objects.create(
            owner=self.user,
            name="Laptop",
            target_amount=Decimal("150000.00"),
        )
        GoalAssignment.objects.create(
            owner=self.user,
            goal=goal,
            amount=Decimal("150000.00"),
        )
        self.create_transaction(
            account,
            "expense",
            "150000.00",
            date=date(2026, 11, 20),
            goal=goal,
        )
        report = self.api()
        goals = report["goals"]
        self.assertEqual(goals["funding_total"], "150000.00")
        self.assertEqual(goals["assigned_total"], "150000.00")
        self.assertEqual(goals["consumed_total"], "150000.00")
        self.assertEqual(goals["available"], "0.00")
        self.assertEqual(goals["rows"][0]["status"], "purchased")
        self.assertEqual(report["cash_flow"]["expenses"], "150000.00")
        self.assertEqual(len(goals["month_purchases"]), 1)

    def test_chart_series_and_categories_are_server_generated(self):
        account = self.create_account(
            opening_balance=Decimal("0.00"),
            opening_balance_date=date(2026, 11, 1),
        )
        self.create_transaction(
            account,
            "income",
            "500.00",
            date=date(2026, 11, 2),
            category="Salary",
        )
        self.create_transaction(
            account,
            "expense",
            "120.00",
            date=date(2026, 11, 3),
            category="Food",
        )
        report = self.api()
        self.assertEqual(report["series"]["mode"], "day")
        self.assertEqual(len(report["series"]["buckets"]), 30)
        self.assertEqual(report["series"]["buckets"][1]["income"], "500.00")
        self.assertEqual(report["categories"]["income"]["list"][0]["category"], "Salary")
        self.assertEqual(report["categories"]["expense"]["list"][0]["category"], "Food")
        self.assertEqual(report["categories"]["expense"]["list"][0]["percentage"], "100.00")

    def test_yearly_report_aggregates_twelve_months(self):
        account = self.create_account(
            opening_balance=Decimal("100.00"),
            opening_balance_date=date(2025, 1, 1),
        )
        self.create_transaction(
            account, "income", "1000.00", date=date(2025, 1, 15)
        )
        self.create_transaction(
            account, "expense", "200.00", date=date(2025, 6, 10)
        )
        self.create_transaction(
            account, "income", "3000.00", date=date(2025, 12, 31)
        )
        self.create_transaction(
            account, "income", "9000.00", date=date(2026, 1, 1)
        )
        report = self.api(mode="year", period="2025")
        self.assertEqual(report["cash_flow"]["income"], "4000.00")
        self.assertEqual(report["cash_flow"]["expenses"], "200.00")
        self.assertEqual(report["cash_flow"]["net"], "3800.00")
        self.assertEqual(report["balances"]["opening"], "100.00")
        self.assertEqual(report["balances"]["closing"], "3900.00")
        self.assertEqual(report["series"]["mode"], "month")
        self.assertEqual(len(report["series"]["buckets"]), 12)
        self.assertEqual(report["series"]["buckets"][0]["income"], "1000.00")
        self.assertEqual(report["series"]["buckets"][5]["expenses"], "200.00")
        self.assertEqual(report["series"]["buckets"][11]["income"], "3000.00")

    def test_yearly_budget_and_savings_breakdown(self):
        self.add_allocation("2025-01", "40000.00", "Savings")
        self.add_allocation("2025-02", "60000.00", "Savings")
        SavingsWithdrawal.objects.create(
            owner=self.user,
            amount=Decimal("15000.00"),
            date=date(2025, 2, 15),
            reason="Test",
        )
        report = self.api(mode="year", period="2025")
        self.assertEqual(report["savings"]["contributions"], "100000.00")
        self.assertEqual(report["savings"]["withdrawalTotal"], "15000.00")
        self.assertEqual(report["savings"]["cumulative_balance"], "85000.00")
        self.assertEqual(len(report["budget"]["monthly"]), 12)
        self.assertEqual(report["budget"]["total_allocated"], "100000.00")
        self.assertEqual(len(report["savings"]["months"]), 12)

    def test_pdf_matches_month_and_filename(self):
        response = self.client.get(
            reverse("core:insights_pdf"), {"mode": "month", "period": "2026-11"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        match = re.search(
            r'filename="Gabsweb-Monthly-Report-(\d{4}-\d{2}-\d{2})\.pdf"',
            response["Content-Disposition"],
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), date.today().isoformat())
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn(b"Poppins", response.content)
        self.assertIn(b"Bricolage", response.content)

    def test_yearly_pdf_is_valid(self):
        self.add_allocation("2025-01", "40000.00", "Savings")
        response = self.client.get(
            reverse("core:insights_pdf"), {"mode": "year", "period": "2025"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        match = re.search(
            r'filename="Gabsweb-Yearly-Report-(\d{4}-\d{2}-\d{2})\.pdf"',
            response["Content-Disposition"],
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), date.today().isoformat())

    def test_empty_report_pdf_still_renders(self):
        response = self.client.get(
            reverse("core:insights_pdf"), {"mode": "month", "period": "2026-10"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 1000)

    def test_pdf_period_text_and_date_formatting(self):
        report = self.api()
        label, comparison = _range_text(report)
        self.assertTrue(label)
        self.assertIsInstance(comparison, str)
        self.assertEqual(_date(date(2026, 11, 11)), "11 Nov 2026")
        self.assertEqual(_date(None), "—")

    def test_pdf_chart_data_matches_report_mode(self):
        account = self.create_account(
            opening_balance=Decimal("0.00"),
            opening_balance_date=date(2025, 1, 1),
        )
        self.create_transaction(
            account, "income", "100.00", date=date(2026, 11, 2)
        )
        self.create_transaction(
            account, "income", "1000.00", date=date(2025, 1, 15)
        )
        monthly = self.api()
        yearly = self.api(mode="year", period="2025")
        monthly_chart = _chart(monthly).contents[0]
        yearly_chart = _chart(yearly).contents[0]
        self.assertEqual(len(monthly_chart.data[0]), 30)
        self.assertEqual(len(yearly_chart.data[0]), 12)
        self.assertEqual(len(monthly_chart.categoryAxis.categoryNames), 30)
        self.assertEqual(len(yearly_chart.categoryAxis.categoryNames), 12)
        self.assertEqual(
            [
                index
                for index, label in enumerate(monthly_chart.categoryAxis.categoryNames)
                if label
            ],
            list(range(0, 30, 3)),
        )
        self.assertEqual(
            [
                index
                for index, label in enumerate(yearly_chart.categoryAxis.categoryNames)
                if label
            ],
            list(range(12)),
        )
        self.assertEqual(monthly_chart.categoryAxis.labels.fontName, "Poppins")
        self.assertEqual(yearly_chart.categoryAxis.labels.fontName, "Poppins")
        for index in range(2):
            self.assertEqual(
                monthly_chart.bars[index].fillColor.hexval(),
                yearly_chart.bars[index].fillColor.hexval(),
            )

    def test_long_report_paginates_and_repeats_headers(self):
        account = self.create_account(
            opening_balance=Decimal("1000000.00"),
            opening_balance_date=date(2026, 11, 1),
        )
        for index in range(40):
            self.create_transaction(
                account,
                "expense",
                "1000.00",
                date=date(2026, 11, (index % 28) + 1),
                category=f"Category {index}",
                description=f"Expense record {index}",
            )
        for index in range(20):
            self.add_allocation("2026-11", "5000.00", f"Allocation {index}")
        response = self.client.get(
            reverse("core:insights_pdf"), {"mode": "month", "period": "2026-11"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        match = re.search(rb"/Count\s+(\d+)", response.content)
        self.assertIsNotNone(match)
        page_count = int(match.group(1))
        self.assertGreaterEqual(page_count, 3)
        self.assertEqual(
            len(re.findall(rb"/Type\s*/Page[^s]", response.content)), page_count
        )

    def test_reports_do_not_modify_financial_records(self):
        account = self.create_account(opening_balance=Decimal("0.00"))
        self.create_transaction(account, "income", "500.00", date=date(2026, 11, 2))
        self.add_allocation("2026-11", "200.00", "Savings")
        SavingsWithdrawal.objects.create(
            owner=self.user,
            amount=Decimal("50.00"),
            date=date(2026, 11, 3),
            reason="Test",
        )
        before = {
            "transactions": Transaction.objects.count(),
            "allocations": BudgetAllocation.objects.count(),
            "withdrawals": SavingsWithdrawal.objects.count(),
            "accounts": Account.objects.count(),
        }
        self.client.get(reverse("core:insights_api"))
        self.client.get(reverse("core:insights_pdf"))
        after = {
            "transactions": Transaction.objects.count(),
            "allocations": BudgetAllocation.objects.count(),
            "withdrawals": SavingsWithdrawal.objects.count(),
            "accounts": Account.objects.count(),
        }
        self.assertEqual(before, after)

    def test_service_returns_none_for_invalid_periods(self):
        self.assertIsNone(reports_service.build_period_report(self.user, "month", "bad"))
        self.assertIsNone(reports_service.build_period_report(self.user, "year", "bad"))
