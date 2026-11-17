import json
from datetime import date
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse

from .models import Account, Transaction
from .test_accounts import AccountTestBase


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class TransactionApiTests(AccountTestBase):
    def post_json(self, url, payload):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_authentication_is_required(self):
        response = self.client.get(reverse("core:transactions"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_income_expense_and_transfer_update_balances(self):
        bank = self.create_account(
            name="Bank", opening_balance=Decimal("100.00")
        )
        cash = self.create_account(
            name="Cash",
            account_type=Account.AccountType.CASH,
            opening_balance=Decimal("20.00"),
        )
        self.client.force_login(self.user)

        income = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "income",
                "amount": "50.00",
                "date": "2026-11-02",
                "account": str(bank.pk),
                "description": "Salary",
                "category": "Salary",
                "payment_method": "Bank transfer",
                "notes": "November",
            },
        )
        self.assertEqual(income.status_code, 201, income.content)
        expense = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "expense",
                "amount": "10.50",
                "date": "2026-11-03",
                "account": str(bank.pk),
                "description": "Groceries",
            },
        )
        self.assertEqual(expense.status_code, 201, expense.content)
        transfer = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "transfer",
                "amount": "15.00",
                "date": "2026-11-04",
                "account": str(bank.pk),
                "destination_account": str(cash.pk),
                "description": "Cash withdrawal",
            },
        )
        self.assertEqual(transfer.status_code, 201, transfer.content)

        bank.refresh_from_db()
        cash.refresh_from_db()
        self.assertEqual(bank.balance, Decimal("124.50"))
        self.assertEqual(cash.balance, Decimal("35.00"))
        self.assertEqual(
            bank.balance + cash.balance,
            Decimal("159.50"),
        )

    def test_transfer_does_not_change_total_wealth(self):
        bank = self.create_account(
            name="Bank", opening_balance=Decimal("500.00")
        )
        cash = self.create_account(
            name="Cash",
            account_type=Account.AccountType.CASH,
            opening_balance=Decimal("10.00"),
        )
        total_before = bank.balance + cash.balance
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "transfer",
                "amount": "75.25",
                "date": "2026-11-05",
                "account": str(bank.pk),
                "destination_account": str(cash.pk),
                "description": "Move money",
            },
        )
        self.assertEqual(response.status_code, 201)
        bank.refresh_from_db()
        cash.refresh_from_db()
        self.assertEqual(bank.balance + cash.balance, total_before)

    def test_invalid_transfers_are_rejected(self):
        bank = self.create_account()
        self.client.force_login(self.user)
        same_account = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "transfer",
                "amount": "10.00",
                "date": "2026-11-05",
                "account": str(bank.pk),
                "destination_account": str(bank.pk),
            },
        )
        self.assertEqual(same_account.status_code, 400)
        missing = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "transfer",
                "amount": "10.00",
                "date": "2026-11-05",
                "account": str(bank.pk),
            },
        )
        self.assertEqual(missing.status_code, 400)
        self.assertFalse(Transaction.objects.exists())

    def test_cannot_use_another_users_account(self):
        mine = self.create_account()
        theirs = self.create_account(user=self.other_user, name="Theirs")
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "expense",
                "amount": "5.00",
                "date": "2026-11-05",
                "account": str(theirs.pk),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Transaction.objects.filter(owner=self.user).exists())
        self.assertEqual(mine.balance, Decimal("1250.50"))

    def test_list_is_owner_scoped_and_ordered(self):
        mine = self.create_account()
        theirs = self.create_account(user=self.other_user, name="Theirs")
        older = self.create_transaction(
            mine, "income", "10.00", date=date(2026, 11, 2)
        )
        newer = self.create_transaction(
            mine, "expense", "5.00", date=date(2026, 11, 4)
        )
        self.create_transaction(theirs, "income", "99.00")
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:transactions"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(
            [item["id"] for item in payload["transactions"]],
            [str(newer.pk), str(older.pk)],
        )

    def test_archived_account_cannot_receive_new_transactions(self):
        account = self.create_account(status=Account.Status.ARCHIVED)
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "income",
                "amount": "10.00",
                "date": "2026-11-05",
                "account": str(account.pk),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("account", response.json()["errors"])

    def test_client_reference_makes_create_idempotent(self):
        account = self.create_account()
        self.client.force_login(self.user)
        payload = {
            "transaction_type": "expense",
            "amount": "12.00",
            "date": "2026-11-05",
            "account": str(account.pk),
            "description": "Lunch",
            "clientReference": "mobile-tap-123",
        }
        first = self.post_json(reverse("core:transactions"), payload)
        second = self.post_json(reverse("core:transactions"), payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(Transaction.objects.filter(owner=self.user).count(), 1)
        self.assertEqual(first.json()["transaction"]["id"], second.json()["transaction"]["id"])

    def test_edit_and_delete_recalculate_balances(self):
        bank = self.create_account(opening_balance=Decimal("100.00"))
        cash = self.create_account(
            name="Cash",
            account_type=Account.AccountType.CASH,
            opening_balance=Decimal("0.00"),
        )
        self.client.force_login(self.user)
        created = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "expense",
                "amount": "30.00",
                "date": "2026-11-05",
                "account": str(bank.pk),
                "description": "Original",
            },
        ).json()["transaction"]
        entry_id = created["id"]

        edited = self.post_json(
            reverse("core:transaction_detail", args=[entry_id]),
            {
                "transaction_type": "transfer",
                "amount": "10.00",
                "date": "2026-11-05",
                "account": str(bank.pk),
                "destination_account": str(cash.pk),
                "description": "Edited",
            },
        )
        self.assertEqual(edited.status_code, 200, edited.content)
        bank.refresh_from_db()
        cash.refresh_from_db()
        self.assertEqual(bank.balance, Decimal("90.00"))
        self.assertEqual(cash.balance, Decimal("10.00"))

        deleted = self.client.post(
            reverse("core:transaction_delete", args=[entry_id])
        )
        self.assertEqual(deleted.status_code, 200)
        bank.refresh_from_db()
        cash.refresh_from_db()
        self.assertEqual(bank.balance, Decimal("100.00"))
        self.assertEqual(cash.balance, Decimal("0.00"))
        self.assertFalse(Transaction.objects.filter(pk=entry_id).exists())

    def test_cannot_edit_or_delete_another_users_transaction(self):
        theirs = self.create_account(user=self.other_user, name="Theirs")
        entry = self.create_transaction(theirs, "income", "20.00")
        self.client.force_login(self.user)
        edit = self.post_json(
            reverse("core:transaction_detail", args=[entry.pk]),
            {
                "transaction_type": "expense",
                "amount": "1.00",
                "date": "2026-11-05",
                "account": str(theirs.pk),
            },
        )
        self.assertEqual(edit.status_code, 404)
        delete = self.client.post(
            reverse("core:transaction_delete", args=[entry.pk])
        )
        self.assertEqual(delete.status_code, 404)
        self.assertTrue(Transaction.objects.filter(pk=entry.pk).exists())

    def test_non_transfer_ignores_destination_account(self):
        bank = self.create_account()
        cash = self.create_account(
            name="Cash", account_type=Account.AccountType.CASH
        )
        self.client.force_login(self.user)
        response = self.post_json(
            reverse("core:transactions"),
            {
                "transaction_type": "income",
                "amount": "10.00",
                "date": "2026-11-05",
                "account": str(bank.pk),
                "destination_account": str(cash.pk),
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        entry = Transaction.objects.get(owner=self.user)
        self.assertIsNone(entry.destination_account)
