import json
import re
from datetime import date
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .forms import AccountForm
from .models import Account, Transaction, User


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AccountTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com", password="StrongPass123!"
        )
        self.other_user = User.objects.create_user(
            email="other@example.com", password="StrongPass123!"
        )

    def create_account(self, user=None, **overrides):
        values = {
            "owner": user or self.user,
            "name": "Main bank",
            "account_type": Account.AccountType.BANK,
            "opening_balance": Decimal("1250.50"),
            "opening_balance_date": date(2026, 11, 1),
        }
        values.update(overrides)
        return Account.objects.create(**values)

    def create_transaction(self, account, transaction_type, amount, **overrides):
        values = {
            "owner": account.owner,
            "transaction_type": transaction_type,
            "account": account,
            "amount": Decimal(amount),
            "date": date(2026, 11, 2),
        }
        values.update(overrides)
        return Transaction.objects.create(**values)


class AccountManagementTests(AccountTestBase):
    def test_multiple_accounts_belong_to_one_user(self):
        first = self.create_account(name="Main bank")
        second = self.create_account(
            name="Cash wallet",
            account_type=Account.AccountType.CASH,
            opening_balance=Decimal("50.00"),
        )
        self.assertEqual(self.user.financial_accounts.count(), 2)
        self.assertEqual(
            set(self.user.financial_accounts.values_list("pk", flat=True)),
            {first.pk, second.pk},
        )

    def test_opening_balance_is_preserved_and_not_income(self):
        account = self.create_account(opening_balance=Decimal("980.75"))
        self.assertEqual(account.opening_balance, Decimal("980.75"))
        self.assertEqual(account.opening_balance_date, date(2026, 11, 1))
        self.assertEqual(account.balance, Decimal("980.75"))
        self.assertEqual(Transaction.objects.count(), 0)

    def test_different_accounts_keep_different_opening_balances(self):
        bank = self.create_account(name="Bank", opening_balance=Decimal("400.00"))
        cash = self.create_account(
            name="Cash",
            account_type=Account.AccountType.CASH,
            opening_balance=Decimal("25.25"),
        )
        self.assertEqual(bank.balance, Decimal("400.00"))
        self.assertEqual(cash.balance, Decimal("25.25"))

    def test_account_types_distinguish_bank_and_cash(self):
        bank = self.create_account(name="Bank", account_type="bank")
        cash = self.create_account(name="Cash", account_type="cash")
        self.assertEqual(bank.get_account_type_display(), "Bank account")
        self.assertEqual(cash.get_account_type_display(), "Cash wallet")

    def test_archiving_preserves_the_account_and_history(self):
        account = self.create_account()
        self.create_transaction(account, "expense", "20.00")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:account_status", args=[account.pk]),
            data=json.dumps({"status": "archived"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        account.refresh_from_db()
        self.assertEqual(account.status, Account.Status.ARCHIVED)
        self.assertEqual(account.transactions.count(), 1)
        self.assertEqual(account.balance, Decimal("1230.50"))

    def test_account_with_history_cannot_be_deleted(self):
        account = self.create_account()
        self.create_transaction(account, "income", "10.00")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:account_delete", args=[account.pk])
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Account.objects.filter(pk=account.pk).exists())

    def test_unused_account_can_be_deleted(self):
        account = self.create_account()
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:account_delete", args=[account.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Account.objects.filter(pk=account.pk).exists())


class AccountBalanceTests(AccountTestBase):
    def test_income_increases_balance(self):
        account = self.create_account(opening_balance=Decimal("100.00"))
        self.create_transaction(account, "income", "250.25")
        self.assertEqual(account.calculate_balance(), Decimal("350.25"))

    def test_expense_decreases_balance(self):
        account = self.create_account(opening_balance=Decimal("100.00"))
        self.create_transaction(account, "expense", "40.10")
        self.assertEqual(account.calculate_balance(), Decimal("59.90"))

    def test_transfer_preserves_total_wealth(self):
        bank = self.create_account(name="Bank", opening_balance=Decimal("500.00"))
        cash = self.create_account(
            name="Cash",
            account_type=Account.AccountType.CASH,
            opening_balance=Decimal("50.00"),
        )
        self.create_transaction(
            bank,
            "transfer",
            "125.00",
            destination_account=cash,
        )
        self.assertEqual(bank.calculate_balance(), Decimal("375.00"))
        self.assertEqual(cash.calculate_balance(), Decimal("175.00"))
        self.assertEqual(
            bank.calculate_balance() + cash.calculate_balance(),
            Decimal("550.00"),
        )

    def test_transactions_before_opening_date_are_excluded(self):
        account = self.create_account(opening_balance=Decimal("100.00"))
        self.create_transaction(
            account, "income", "999.00", date=date(2026, 10, 31)
        )
        self.assertEqual(account.calculate_balance(), Decimal("100.00"))

    def test_other_account_transactions_do_not_change_balance(self):
        account = self.create_account(name="Main")
        other = self.create_account(name="Other", opening_balance=Decimal("10.00"))
        self.create_transaction(other, "expense", "5.00")
        self.assertEqual(account.calculate_balance(), Decimal("1250.50"))
        self.assertEqual(other.calculate_balance(), Decimal("5.00"))

    def test_decimal_precision_is_maintained(self):
        account = self.create_account(opening_balance=Decimal("0.10"))
        self.create_transaction(account, "income", "0.20")
        self.assertEqual(account.calculate_balance(), Decimal("0.30"))

    def test_allocations_and_estimated_costs_are_not_balances(self):
        account = self.create_account(opening_balance=Decimal("300.00"))
        self.create_transaction(account, "expense", "30.00")
        self.assertEqual(account.calculate_balance(), Decimal("270.00"))


class AccountOwnershipTests(AccountTestBase):
    def test_list_returns_only_authenticated_users_accounts(self):
        self.create_account()
        self.create_account(owner=self.other_user, name="Other only")
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:accounts"))
        self.assertEqual(response.status_code, 200)
        names = [item["name"] for item in response.json()["accounts"]]
        self.assertEqual(names, ["Main bank"])

    def test_anonymous_access_is_redirected(self):
        response = self.client.get(reverse("core:accounts"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_cross_user_detail_is_not_found(self):
        account = self.create_account(owner=self.other_user)
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("core:account_detail", args=[account.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_cross_user_edit_is_not_found(self):
        account = self.create_account(owner=self.other_user)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:account_detail", args=[account.pk]),
            data=json.dumps({"name": "Hijacked"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        account.refresh_from_db()
        self.assertEqual(account.name, "Main bank")

    def test_cross_user_archive_and_delete_are_not_found(self):
        account = self.create_account(owner=self.other_user)
        self.client.force_login(self.user)
        archive = self.client.post(
            reverse("core:account_status", args=[account.pk]),
            data=json.dumps({"status": "archived"}),
            content_type="application/json",
        )
        delete = self.client.post(
            reverse("core:account_delete", args=[account.pk])
        )
        self.assertEqual(archive.status_code, 404)
        self.assertEqual(delete.status_code, 404)
        self.assertTrue(Account.objects.filter(pk=account.pk).exists())


class AccountValidationTests(AccountTestBase):
    def test_opening_balance_is_required_and_never_defaults_to_zero(self):
        form = AccountForm(
            data={
                "name": "No opening balance",
                "account_type": "bank",
                "opening_balance": "",
                "opening_balance_date": "2026-11-01",
            },
            owner=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("opening_balance", form.errors)

    def test_negative_opening_balance_is_rejected(self):
        form = AccountForm(
            data={
                "name": "Negative",
                "account_type": "bank",
                "opening_balance": "-1.00",
                "opening_balance_date": "2026-11-01",
            },
            owner=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("opening_balance", form.errors)

    def test_more_than_two_decimal_places_is_rejected(self):
        form = AccountForm(
            data={
                "name": "Precision",
                "account_type": "bank",
                "opening_balance": "10.001",
                "opening_balance_date": "2026-11-01",
            },
            owner=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("opening_balance", form.errors)

    def test_api_rejects_invalid_money_and_does_not_create_account(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:accounts"),
            data=json.dumps(
                {
                    "name": "Bad amount",
                    "account_type": "bank",
                    "opening_balance": "not-money",
                    "opening_balance_date": "2026-11-01",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("opening_balance", response.json()["errors"])
        self.assertEqual(Account.objects.count(), 0)

    def test_duplicate_account_name_for_same_owner_is_rejected(self):
        self.create_account(name="Wallet")
        form = AccountForm(
            data={
                "name": " wallet ",
                "account_type": "cash",
                "opening_balance": "1.00",
                "opening_balance_date": "2026-11-01",
            },
            owner=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_different_users_may_reuse_the_same_account_name(self):
        self.create_account(name="Wallet")
        form = AccountForm(
            data={
                "name": "Wallet",
                "account_type": "cash",
                "opening_balance": "1.00",
                "opening_balance_date": "2026-11-01",
            },
            owner=self.other_user,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_create_and_edit_through_api(self):
        self.client.force_login(self.user)
        create = self.client.post(
            reverse("core:accounts"),
            data=json.dumps(
                {
                    "name": "API account",
                    "account_type": "cash",
                    "opening_balance": "123.45",
                    "opening_balance_date": "2026-11-01",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create.status_code, 201)
        account_id = create.json()["account"]["id"]
        self.assertEqual(create.json()["account"]["balance"], "123.45")

        edit = self.client.post(
            reverse("core:account_detail", args=[account_id]),
            data=json.dumps(
                {
                    "name": "Renamed account",
                    "account_type": "cash",
                    "opening_balance": "200.00",
                    "opening_balance_date": "2026-11-01",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.json()["account"]["name"], "Renamed account")
        self.assertEqual(edit.json()["account"]["openingBalance"], "200.00")

    def test_transaction_page_exposes_csrf_token_and_account_ui(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:transaction"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="mf-csrf-token"')
        self.assertContains(response, 'id="mf-account-form"')
        self.assertContains(response, "Add account")

    def test_account_list_returns_server_calculated_balance(self):
        account = self.create_account(opening_balance=Decimal("100.00"))
        self.create_transaction(account, "income", "25.00")
        self.create_transaction(account, "expense", "10.00")
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:accounts"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["accounts"][0]["balance"], "115.00")
        self.assertEqual(payload["totalBalance"], "115.00")
        self.assertEqual(
            payload["accounts"][0]["openingBalance"], "100.00"
        )

    def test_account_edit_response_updates_balance_for_ui(self):
        account = self.create_account(opening_balance=Decimal("100.00"))
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:account_detail", args=[account.pk]),
            data=json.dumps(
                {
                    "name": "Updated account",
                    "account_type": "cash",
                    "opening_balance": "250.00",
                    "opening_balance_date": "2026-11-01",
                    "description": "Updated",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account"]["balance"], "250.00")
        self.assertEqual(response.json()["account"]["type"], "cash")

    def test_archive_status_response_is_ui_ready(self):
        account = self.create_account()
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:account_status", args=[account.pk]),
            data=json.dumps({"status": "archived"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["account"]["isArchived"])
        self.assertEqual(response.json()["account"]["status"], "archived")


class AccountCsrfTests(AccountTestBase):
    def test_account_create_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(
            reverse("core:accounts"),
            data=json.dumps(
                {
                    "name": "No CSRF",
                    "account_type": "bank",
                    "opening_balance": "10.00",
                    "opening_balance_date": "2026-11-01",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Account.objects.count(), 0)

    def test_account_create_succeeds_with_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        page = client.get(reverse("core:transaction"))
        token = re.search(
            r'id="mf-csrf-token" value="([^"]+)"', page.content.decode()
        ).group(1)
        response = client.post(
            reverse("core:accounts"),
            data=json.dumps(
                {
                    "name": "With CSRF",
                    "account_type": "cash",
                    "opening_balance": "10.00",
                    "opening_balance_date": "2026-11-01",
                }
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Account.objects.count(), 1)
