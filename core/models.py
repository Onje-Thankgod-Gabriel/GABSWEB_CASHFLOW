import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.urls import reverse

from .storage import private_profile_image_storage

FINANCIAL_TRACKING_START_DATE = date(2026, 11, 1)
MONEY_MAX_DIGITS = 14
MONEY_DECIMAL_PLACES = 2
ZERO_MONEY = Decimal("0.00")


def _profile_image_upload_path(instance, filename, source):
    extension = Path(filename).suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
        extension = ".jpg"
    user_id = instance.pk or "unassigned"
    return f"profile-images/{user_id}/{source}/{uuid.uuid4().hex}{extension}"


def profile_image_upload_to(instance, filename):
    return _profile_image_upload_path(instance, filename, "manual")


def google_profile_image_upload_to(instance, filename):
    return _profile_image_upload_path(instance, filename, "google")


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).strip().lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class AccountStatus(models.TextChoices):
        GENERAL = "GENERAL", "General"
        PENDING = "PENDING", "Pending workplace approval"
        APPROVED = "APPROVED", "Approved workplace access"

    username = None
    email = models.EmailField("email address", unique=True)
    account_status = models.CharField(
        max_length=10,
        choices=AccountStatus.choices,
        default=AccountStatus.GENERAL,
        db_index=True,
        help_text=(
            "General is the default. Pending means the user works at GCTN or "
            "Apex Pay and is awaiting owner approval. Approved grants the "
            "combined GCTN/Apex Pay workplace privileges."
        ),
    )
    profile_image = models.ImageField(
        upload_to=profile_image_upload_to,
        storage=private_profile_image_storage,
        blank=True,
        null=True,
    )
    google_profile_image = models.ImageField(
        upload_to=google_profile_image_upload_to,
        storage=private_profile_image_storage,
        blank=True,
        null=True,
    )
    google_profile_image_source = models.URLField(max_length=500, blank=True)
    google_profile_image_enabled = models.BooleanField(default=True)
    professional_role = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text=(
            "The user's job title or profession. This is display information "
            "only and never grants application permissions."
        ),
    )

    class WorkplaceCompany(models.TextChoices):
        GCTN = "GCTN", "GCTN"
        APEX = "APEX", "Apex Pay"

    workplace_company = models.CharField(
        max_length=10,
        choices=WorkplaceCompany.choices,
        blank=True,
        default="",
        help_text="The approved GCTN or Apex Pay membership, when known.",
    )
    work_team = models.CharField(max_length=80, blank=True, default="")
    joining_date = models.DateField(blank=True, null=True)
    joining_arrangement = models.CharField(max_length=120, blank=True, default="")
    works_shifts = models.BooleanField(
        default=False,
        help_text="Whether this user personally works shifts.",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def clean(self):
        super().clean()
        role = " ".join((self.professional_role or "").split())
        if len(role) > 80:
            raise ValidationError(
                {"professional_role": "Professional role must be 80 characters or fewer."}
            )
        self.professional_role = role

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        self.professional_role = " ".join(
            (self.professional_role or "").split()
        )[:80]
        super().save(*args, **kwargs)

    @property
    def workplace_label(self):
        if self.workplace_company:
            return self.get_workplace_company_display()
        return "GCTN/Apex Pay"

    @property
    def has_approved_workplace_access(self):
        return self.account_status == self.AccountStatus.APPROVED

    def get_email_display_name(self):
        """Return the email local part, never the full email address."""
        email = (self.email or "").strip()
        if not email:
            return ""
        return email.split("@", 1)[0].strip()

    def get_first_display_name(self):
        """Return the first name only, with safe non-email fallbacks."""
        first_name = (self.first_name or "").strip()
        if first_name:
            return first_name
        full_name = " ".join((self.get_full_name() or "").split())
        if full_name:
            return full_name.split(" ")[0]
        return self.get_email_display_name() or "there"

    def get_two_part_name(self):
        """Return the first two names, then the email local part, then 'there'."""
        full_name = " ".join((self.get_full_name() or "").split())
        if full_name:
            return " ".join(full_name.split(" ")[:2])
        return self.get_email_display_name() or "there"

    @property
    def effective_profile_image(self):
        if self.profile_image:
            return self.profile_image
        if self.google_profile_image_enabled and self.google_profile_image:
            return self.google_profile_image
        return None

    @property
    def effective_profile_image_url(self):
        if not self.effective_profile_image:
            return ""
        return reverse("core:profile_image")

    @property
    def effective_profile_image_version(self):
        image = self.effective_profile_image
        if not image:
            return ""
        return uuid.uuid5(uuid.NAMESPACE_URL, image.name).hex

    @property
    def effective_profile_image_url_versioned(self):
        url = self.effective_profile_image_url
        if not url:
            return ""
        return f"{url}?v={self.effective_profile_image_version}"

    @property
    def uses_google_profile_image(self):
        return bool(
            not self.profile_image
            and self.google_profile_image_enabled
            and self.google_profile_image
        )

    def __str__(self):
        return self.email


class Account(models.Model):
    """A financial account owned by exactly one authenticated user."""

    class AccountType(models.TextChoices):
        BANK = "bank", "Bank account"
        CASH = "cash", "Cash wallet"
        SAVINGS = "savings", "Savings account"
        MOBILE = "mobile", "Mobile money"
        INVESTMENT = "investment", "Investment"
        OTHER = "other", "Other account"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="financial_accounts",
    )
    name = models.CharField(max_length=80)
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.BANK,
    )
    opening_balance = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(ZERO_MONEY)],
        help_text=(
            "Money already held in this account before financial tracking "
            "began. This is a starting position, not income."
        ),
    )
    opening_balance_date = models.DateField(
        default=FINANCIAL_TRACKING_START_DATE,
        help_text="The date the opening balance represents.",
    )
    description = models.CharField(max_length=240, blank=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "name")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(opening_balance__gte=0),
                name="account_opening_balance_non_negative",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.get_account_type_display()})"

    @property
    def type(self):
        """Expose the existing frontend naming without changing storage."""
        return self.account_type

    @property
    def is_archived(self):
        return self.status == self.Status.ARCHIVED

    def clean(self):
        super().clean()
        if self.opening_balance is None:
            raise ValidationError(
                {"opening_balance": "Enter the account's opening balance."}
            )
        if self.opening_balance < ZERO_MONEY:
            raise ValidationError(
                {"opening_balance": "Opening balance cannot be negative."}
            )

    def calculate_balance(self, transactions=None):
        """Return the account balance from opening balance and transactions.

        Income increases the balance, expenses decrease it, and transfers move
        money between accounts without changing total wealth. Only transactions
        on or after the opening balance date are counted, matching the existing
        browser data layer's accounting rules.
        """
        balance = Decimal(self.opening_balance or 0)
        if transactions is None:
            transactions = Transaction.objects.filter(
                Q(account=self) | Q(destination_account=self)
            ).select_related("account", "destination_account")
        for transaction in transactions:
            balance = transaction.balance_result(self, balance)
        return balance.quantize(Decimal("0.01"))

    @property
    def balance(self):
        return self.calculate_balance()


class Transaction(models.Model):
    """Minimal server-side ledger record used to calculate account balances.

    This model deliberately mirrors only the established accounting rules:
    income adds to one account, expenses subtract from one account, and a
    transfer subtracts from a source account and adds to a destination account.
    Allocations, savings and estimated project costs are not financial
    transactions here and therefore never affect account balances.
    """

    class TransactionType(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"
        TRANSFER = "transfer", "Transfer"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="financial_transactions",
    )
    transaction_type = models.CharField(
        max_length=10,
        choices=TransactionType.choices,
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="transactions",
    )
    destination_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="incoming_transfers",
        blank=True,
        null=True,
    )
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    date = models.DateField(default=FINANCIAL_TRACKING_START_DATE)
    category = models.CharField(max_length=80, blank=True)
    description = models.CharField(max_length=240, blank=True)
    payment_method = models.CharField(max_length=40, blank=True)
    notes = models.CharField(max_length=400, blank=True)
    client_reference = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text=(
            "Optional client-generated idempotency reference that prevents "
            "duplicate submissions from slow or repeated form requests."
        ),
    )
    allocation = models.ForeignKey(
        "BudgetAllocation",
        on_delete=models.SET_NULL,
        related_name="linked_transactions",
        blank=True,
        null=True,
        help_text=(
            "The optional monthly budget allocation this expense counts "
            "against. Linking a plan never changes the amount or account."
        ),
    )
    goal = models.OneToOneField(
        "Goal",
        on_delete=models.SET_NULL,
        related_name="purchase_transaction",
        blank=True,
        null=True,
        help_text=(
            "The optional fully funded goal this actual expense purchased. "
            "Linking a goal records the purchase without creating another "
            "transaction or changing the amount."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-date", "-created_at")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="transaction_amount_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(transaction_type="transfer")
                    & models.Q(destination_account__isnull=False)
                )
                | ~models.Q(transaction_type="transfer"),
                name="transfer_requires_destination",
            ),
            # NULL client references are optional idempotency keys. SQLite,
            # MySQL and PostgreSQL all treat NULLs as distinct in a unique
            # index, so an unconditional constraint enforces uniqueness only
            # for real references while allowing any number of NULLs. This is
            # portable to MySQL 8.4, where partial/conditional unique indexes
            # are not supported and Django would silently skip the constraint.
            models.UniqueConstraint(
                fields=("owner", "client_reference"),
                name="unique_owner_transaction_client_reference",
            ),
        ]

    def __str__(self):
        return f"{self.get_transaction_type_display()} {self.amount}"

    @property
    def type(self):
        return self.transaction_type

    def clean(self):
        super().clean()
        if self.amount is None:
            raise ValidationError({"amount": "Enter a transaction amount."})
        if self.amount <= ZERO_MONEY:
            raise ValidationError(
                {"amount": "Transaction amount must be greater than zero."}
            )
        if self.account_id and self.owner_id:
            if self.account.owner_id != self.owner_id:
                raise ValidationError(
                    {"account": "Select one of your own accounts."}
                )
        if self.transaction_type == self.TransactionType.TRANSFER:
            if not self.destination_account_id:
                raise ValidationError(
                    {"destination_account": "Choose a destination account."}
                )
            if self.destination_account_id == self.account_id:
                raise ValidationError(
                    {"destination_account": "Transfer accounts must be different."}
                )
            if (
                self.destination_account_id
                and self.destination_account.owner_id != self.owner_id
            ):
                raise ValidationError(
                    {"destination_account": "Select one of your own accounts."}
                )
        elif self.destination_account_id:
            raise ValidationError(
                {
                    "destination_account": (
                        "Only transfers can have a destination account."
                    )
                }
            )
        if self.allocation_id:
            self._validate_allocation()
        if self.goal_id:
            self._validate_goal()

    def _validate_goal(self):
        goal = self.goal
        if self.transaction_type != self.TransactionType.EXPENSE:
            raise ValidationError(
                {
                    "goal": (
                        "Only expense transactions can be linked to a goal."
                    )
                }
            )
        if self.owner_id and goal.owner_id != self.owner_id:
            raise ValidationError(
                {"goal": "Select one of your own goals."}
            )

    def _validate_allocation(self):
        allocation = self.allocation
        if self.transaction_type != self.TransactionType.EXPENSE:
            raise ValidationError(
                {
                    "allocation": (
                        "Only expense transactions can be linked to a budget "
                        "allocation."
                    )
                }
            )
        if self.owner_id and allocation.budget.owner_id != self.owner_id:
            raise ValidationError(
                {"allocation": "Select one of your own budget allocations."}
            )
        if self.date and allocation.budget.month != self.date.replace(day=1):
            raise ValidationError(
                {
                    "allocation": (
                        "The expense date must fall within the allocation's "
                        "financial month."
                    )
                }
            )

    def applies_to(self, account):
        return (
            self.account_id == account.pk
            or self.destination_account_id == account.pk
        )

    def balance_result(self, account, current):
        if self.date < account.opening_balance_date:
            return current
        if self.transaction_type == self.TransactionType.INCOME:
            if self.account_id == account.pk:
                return current + self.amount
        elif self.transaction_type == self.TransactionType.EXPENSE:
            if self.account_id == account.pk:
                return current - self.amount
        elif self.transaction_type == self.TransactionType.TRANSFER:
            if self.account_id == account.pk:
                current -= self.amount
            if self.destination_account_id == account.pk:
                current += self.amount
        return current


class BudgetCategory(models.Model):
    """A reusable spending or financial purpose owned by one user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="budget_categories",
    )
    name = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "owner",
                name="unique_owner_budget_category_name",
            )
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = " ".join((self.name or "").split())
        super().save(*args, **kwargs)


class MonthlyBudget(models.Model):
    """One budget month per owner, stored as the first day of that month."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="monthly_budgets",
    )
    month = models.DateField(help_text="Always the first day of the budget month.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-month",)
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "month"),
                name="unique_owner_monthly_budget_month",
            )
        ]

    def __str__(self):
        return f"{self.owner_id} {self.month_key}"

    @property
    def month_key(self):
        return self.month.strftime("%Y-%m")

    def save(self, *args, **kwargs):
        if self.month:
            self.month = self.month.replace(day=1)
        super().save(*args, **kwargs)


class BudgetAllocation(models.Model):
    """A planned use of income for one budget month.

    Allocations are plans only. They never create transactions, change account
    balances or count as spending by themselves.
    """

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey(
        MonthlyBudget,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    category = models.ForeignKey(
        BudgetCategory,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    name = models.CharField(max_length=80)
    planned_amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PLANNED,
        db_index=True,
    )
    description = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "created_at")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(planned_amount__gt=0),
                name="budget_allocation_amount_positive",
            )
        ]
        indexes = [
            models.Index(fields=("budget", "status")),
            models.Index(fields=("category",)),
        ]

    def __str__(self):
        return f"{self.name} {self.planned_amount}"

    @property
    def owner(self):
        return self.budget.owner

    @property
    def financial_month(self):
        return self.budget.month

    @property
    def financial_month_key(self):
        return self.budget.month_key

    @property
    def purpose(self):
        return self.category.name

    def clean(self):
        super().clean()
        if self.planned_amount is None:
            raise ValidationError(
                {"planned_amount": "Enter the planned allocation amount."}
            )
        if self.planned_amount <= ZERO_MONEY:
            raise ValidationError(
                {"planned_amount": "Planned amount must be greater than zero."}
            )
        if self.category_id and self.budget_id:
            if self.category.owner_id != self.budget.owner_id:
                raise ValidationError(
                    {"category": "Select one of your own budget categories."}
                )

    def calculate_actual_spending(self):
        total = self.linked_transactions.filter(
            transaction_type=Transaction.TransactionType.EXPENSE,
            date__gte=self.budget.month,
        ).filter(date__lt=_next_month_start(self.budget.month))
        return sum((entry.amount for entry in total), ZERO_MONEY)

    @property
    def actual_spending(self):
        return self.calculate_actual_spending()

    @property
    def remaining_amount(self):
        return Decimal(self.planned_amount) - self.actual_spending



class Goal(models.Model):
    """A planned purchase funded from the cumulative Budget Goals pool."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="financial_goals",
    )
    name = models.CharField(max_length=80)
    target_amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    target_date = models.DateField(blank=True, null=True)
    description = models.CharField(max_length=300, blank=True)
    client_reference = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text=(
            "Optional client-generated idempotency reference that prevents "
            "duplicate submissions from slow or repeated requests."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "name")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target_amount__gt=0),
                name="goal_target_amount_positive",
            ),
            models.UniqueConstraint(
                fields=("owner", "client_reference"),
                name="unique_owner_goal_client_reference",
            ),
        ]
        indexes = [models.Index(fields=("owner", "created_at"))]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        self.name = " ".join((self.name or "").split())
        if not self.name:
            raise ValidationError({"name": "Enter a goal name."})
        if len(self.name) > 80:
            raise ValidationError(
                {"name": "Goal name must be 80 characters or fewer."}
            )
        if self.target_amount is None:
            raise ValidationError(
                {"target_amount": "Enter a target amount."}
            )
        if self.target_amount <= ZERO_MONEY:
            raise ValidationError(
                {"target_amount": "Target amount must be greater than zero."}
            )
        self.description = " ".join((self.description or "").split())
        if len(self.description) > 300:
            raise ValidationError(
                {"description": "Description must be 300 characters or fewer."}
            )


class GoalAssignment(models.Model):
    """An allocation of existing Goals funding to one goal.

    Assignments are planning records only. They never create income, expenses,
    transfers, Savings contributions or account-balance changes. Reversing an
    assignment keeps the original row and marks it reversed so history remains
    traceable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="goal_assignments",
    )
    goal = models.ForeignKey(
        Goal,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    client_reference = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text=(
            "Optional client-generated idempotency reference that prevents "
            "duplicate submissions from slow or repeated requests."
        ),
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Set when a recorded goal purchase releases this assignment from "
            "active reserved funding. The row is kept as history."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="goal_assignment_amount_positive",
            ),
            models.UniqueConstraint(
                fields=("owner", "client_reference"),
                name="unique_owner_goal_assignment_reference",
            ),
        ]
        indexes = [
            models.Index(fields=("owner", "goal", "reversed_at")),
            models.Index(fields=("owner", "created_at")),
        ]

    def __str__(self):
        return f"{self.goal_id} {self.amount}"

    @property
    def is_active(self):
        return self.reversed_at is None and self.released_at is None

    @property
    def is_released(self):
        return self.reversed_at is None and self.released_at is not None

    def clean(self):
        super().clean()
        if self.amount is None:
            raise ValidationError({"amount": "Enter an amount to assign."})
        if self.amount <= ZERO_MONEY:
            raise ValidationError(
                {"amount": "Assignment amount must be greater than zero."}
            )
        if self.goal_id and self.owner_id:
            if self.goal.owner_id != self.owner_id:
                raise ValidationError(
                    {"goal": "Select one of your own goals."}
                )


class SavingsWithdrawal(models.Model):
    """Money taken out of the cumulative Budget-funded savings balance.

    Withdrawals are records only. They never create income, expenses,
    transfers or account-balance changes. The monthly amount saved is always
    derived from the user's Budget Savings allocations.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="savings_withdrawals",
    )
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    date = models.DateField(default=FINANCIAL_TRACKING_START_DATE)
    reason = models.CharField(max_length=300)
    client_reference = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text=(
            "Optional client-generated idempotency reference that prevents "
            "duplicate submissions from slow or repeated requests."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-date", "-created_at")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="savings_withdrawal_amount_positive",
            ),
            models.UniqueConstraint(
                fields=("owner", "client_reference"),
                name="unique_owner_savings_withdrawal_reference",
            ),
        ]
        indexes = [
            models.Index(fields=("owner", "date")),
            models.Index(fields=("owner", "created_at")),
        ]

    def __str__(self):
        return f"Withdrawal {self.amount}"

    def clean(self):
        super().clean()
        if self.amount is None:
            raise ValidationError(
                {"amount": "Enter the amount you withdrew."}
            )
        if self.amount <= ZERO_MONEY:
            raise ValidationError(
                {"amount": "Withdrawal amount must be greater than zero."}
            )
        self.reason = " ".join((self.reason or "").split())
        if not self.reason:
            raise ValidationError(
                {"reason": "Enter what you used the withdrawn money for."}
            )
        if len(self.reason) > 300:
            raise ValidationError(
                {"reason": "Reason must be 300 characters or fewer."}
            )


class Notification(models.Model):
    """An in-app notification owned by exactly one authenticated user."""

    class EventType(models.TextChoices):
        MEMBERSHIP_APPROVED = "membership_approved", "Membership approved"
        WELCOME = "welcome", "Welcome"
        LOGIN = "login", "Login"
        ACCOUNT = "account", "Account"

    class EmailStatus(models.TextChoices):
        NOT_REQUIRED = "not_required", "Not required"
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=120)
    message = models.CharField(max_length=500)
    event_type = models.CharField(
        max_length=40,
        choices=EventType.choices,
        default=EventType.ACCOUNT,
    )
    dedupe_key = models.CharField(
        max_length=120,
        null=True,
        blank=True,
        help_text=(
            "Optional event key that guarantees one notification per owner "
            "and event even if a save or request is repeated."
        ),
    )
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    email_status = models.CharField(
        max_length=16,
        choices=EmailStatus.choices,
        default=EmailStatus.NOT_REQUIRED,
        db_index=True,
    )
    email_attempts = models.PositiveSmallIntegerField(default=0)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    email_last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "dedupe_key"),
                name="unique_owner_notification_dedupe_key",
            )
        ]
        indexes = [models.Index(fields=("owner", "is_read", "created_at"))]

    def __str__(self):
        return f"{self.owner_id}: {self.title}"


class UserSupervisor(models.Model):
    """A manually entered supervisor belonging to one user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="supervisors",
    )
    name = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "created_at")
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "owner",
                name="unique_owner_supervisor_name",
            )
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = " ".join((self.name or "").split())
        super().save(*args, **kwargs)


class PersonalShift(models.Model):
    """A user-defined shift that is independent of GCTN/Apex Pay rules."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="personal_shifts",
    )
    name = models.CharField(max_length=40)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "start_time")
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "owner",
                name="unique_owner_personal_shift_name",
            )
        ]

    def __str__(self):
        return f"{self.name} {self.start_time}-{self.end_time}"

    def clean(self):
        super().clean()
        if not " ".join((self.name or "").split()):
            raise ValidationError({"name": "Enter a shift name."})
        if self.start_time and self.end_time and self.start_time == self.end_time:
            raise ValidationError(
                {"end_time": "Shift start and end times must be different."}
            )

    def save(self, *args, **kwargs):
        self.name = " ".join((self.name or "").split())
        super().save(*args, **kwargs)


def default_work_shift_sequence():
    return ["morning", "afternoon", "night"]


def default_work_shift_times():
    return {
        "morning": {"start": "07:00", "end": "13:00"},
        "afternoon": {"start": "13:00", "end": "19:00"},
        "night": {"start": "19:00", "end": "07:00"},
    }


class WorkScheduleRecord(models.Model):
    """A confirmed schedule entry owned by exactly one user.

    Manual records are authoritative; generated records are predictions that
    the employee explicitly accepted.  A date has at most one record per owner.
    """

    class ShiftType(models.TextChoices):
        MORNING = "morning", "Morning"
        AFTERNOON = "afternoon", "Afternoon"
        NIGHT = "night", "Night"
        OFF = "off", "Day off"

    class Status(models.TextChoices):
        WORKING = "working", "Working day"
        OFF = "off", "Day off"
        UNASSIGNED = "unassigned", "Unassigned"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        GENERATED = "generated", "Generated"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="work_schedule_records",
    )
    date = models.DateField(db_index=True)
    shift_type = models.CharField(max_length=10, choices=ShiftType.choices)
    status = models.CharField(max_length=10, choices=Status.choices)
    start_time = models.TimeField(blank=True, null=True)
    end_time = models.TimeField(blank=True, null=True)
    team = models.CharField(max_length=80, blank=True, default="")
    notes = models.CharField(max_length=300, blank=True, default="")
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.MANUAL
    )
    is_exception = models.BooleanField(
        default=False,
        help_text=(
            "A confirmed one-off exception that is kept without changing the "
            "employee's normal rotation position."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("date", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "date"),
                name="unique_owner_work_schedule_date",
            )
        ]

    def __str__(self):
        return f"{self.owner_id} {self.date} {self.shift_type}"

    def clean(self):
        super().clean()
        if self.status == self.Status.OFF:
            if self.shift_type != self.ShiftType.OFF:
                raise ValidationError(
                    {"shift_type": "A day off must use the off shift type."}
                )
            if self.start_time or self.end_time:
                raise ValidationError(
                    {"start_time": "A day off cannot have working times."}
                )
        elif self.status == self.Status.WORKING:
            if self.shift_type == self.ShiftType.OFF:
                raise ValidationError(
                    {"shift_type": "Choose a working shift or mark the day off."}
                )
            if not self.start_time or not self.end_time:
                raise ValidationError(
                    {"start_time": "Working entries need a start and end time."}
                )
            if self.start_time == self.end_time:
                raise ValidationError(
                    {"end_time": "Shift start and end times cannot be the same."}
                )

    def save(self, *args, **kwargs):
        self.team = " ".join((self.team or "").split())[:80]
        self.notes = " ".join((self.notes or "").split())[:300]
        super().save(*args, **kwargs)


class WorkRotationConfig(models.Model):
    """Per-employee rotation setup; never shared between employees."""

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="work_rotation_config",
    )
    configured = models.BooleanField(default=False)
    working_days_per_cycle = models.PositiveSmallIntegerField(default=3)
    off_days_per_cycle = models.PositiveSmallIntegerField(default=2)
    shift_sequence = models.JSONField(default=default_work_shift_sequence)
    shift_times = models.JSONField(default=default_work_shift_times)
    anchor_date = models.DateField(blank=True, null=True)
    anchor_shift = models.CharField(
        max_length=10, choices=WorkScheduleRecord.ShiftType.choices, blank=True
    )
    anchor_position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "work rotation configuration"

    def __str__(self):
        return f"{self.owner_id} rotation"


def _next_month_start(month):
    if month.month == 12:
        return date(month.year + 1, 1, 1)
    return date(month.year, month.month + 1, 1)


class DailyPlan(models.Model):
    """One day's planner record owned by exactly one authenticated user.

    A user has at most one plan per calendar date. Tasks, appointments and the
    Top Three priority references all belong to this plan and therefore to the
    same owner and date.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="daily_plans",
    )
    date = models.DateField()
    day_status = models.CharField(
        max_length=20, blank=True, default="unspecified"
    )
    notes = models.TextField(blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("date", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "date"),
                name="unique_owner_daily_plan_date",
            )
        ]
        indexes = [models.Index(fields=("owner", "date"))]

    def __str__(self):
        return f"{self.owner_id} {self.date}"

    @property
    def top_three_task_ids(self):
        return [
            str(entry.task_id)
            for entry in self.top_three_entries.order_by("position")
        ]

    def score(self):
        tasks = list(self.tasks.all())
        total = len(tasks)
        completed = sum(1 for task in tasks if task.completed)
        if total == 0:
            return {
                "available": False,
                "percentage": None,
                "completedTasks": 0,
                "totalTasks": 0,
                "label": "No tasks scheduled",
            }
        percentage = round((completed / total) * 100, 2)
        return {
            "available": True,
            "percentage": percentage,
            "completedTasks": completed,
            "totalTasks": total,
        }


class DailyTask(models.Model):
    """A task in one user's daily plan."""

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(
        DailyPlan,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True, default="")
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.MEDIUM
    )
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("position", "created_at")
        indexes = [models.Index(fields=("plan", "position"))]

    def __str__(self):
        return self.title


class DailyMeeting(models.Model):
    """An appointment attached to a single daily plan date."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(
        DailyPlan,
        on_delete=models.CASCADE,
        related_name="meetings",
    )
    title = models.CharField(max_length=180)
    date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    location = models.CharField(max_length=240, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("start_time", "created_at")
        indexes = [models.Index(fields=("plan", "date"))]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValidationError(
                {"end_time": "End time must not be before the start time."}
            )


class DailyPlanTopThree(models.Model):
    """An ordered reference from a daily plan to one of its existing tasks."""

    plan = models.ForeignKey(
        DailyPlan,
        on_delete=models.CASCADE,
        related_name="top_three_entries",
    )
    task = models.ForeignKey(
        DailyTask,
        on_delete=models.CASCADE,
        related_name="top_three_entries",
    )
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=("plan", "task"),
                name="unique_daily_plan_top_three_task",
            ),
            models.UniqueConstraint(
                fields=("plan", "position"),
                name="unique_daily_plan_top_three_position",
            ),
            models.CheckConstraint(
                condition=models.Q(position__gte=0, position__lte=2),
                name="daily_plan_top_three_position_range",
            ),
        ]

    def __str__(self):
        return f"{self.plan_id} #{self.position} -> {self.task_id}"

    def clean(self):
        super().clean()
        if self.task_id and self.plan_id:
            if self.task.plan_id != self.plan_id:
                raise ValidationError(
                    {"task": "Top Three tasks must belong to the same daily plan."}
                )


class Project(models.Model):
    """A planning-only project owned by exactly one authenticated user.

    Estimated cost is planning information. It is deliberately not linked to
    any financial account, transaction, budget, savings plan or goal.
    """

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        ON_HOLD = "on-hold", "On hold"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="planning_projects",
    )
    client_reference = models.CharField(
        max_length=120,
        null=True,
        blank=True,
        help_text="Original browser id used to keep legacy imports idempotent.",
    )
    name = models.CharField(max_length=180)
    objectives = models.TextField(blank=True, default="")
    execution_plan = models.TextField(blank=True, default="")
    start_date = models.DateField()
    end_date = models.DateField()
    deadline = models.DateField()
    estimated_cost = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO_MONEY,
        validators=[MinValueValidator(ZERO_MONEY)],
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PLANNED
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "client_reference"),
                name="unique_owner_project_client_reference",
            ),
            models.CheckConstraint(
                condition=models.Q(estimated_cost__gte=0),
                name="project_estimated_cost_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="project_end_date_on_or_after_start",
            ),
            models.CheckConstraint(
                condition=models.Q(deadline__gte=models.F("start_date")),
                name="project_deadline_on_or_after_start",
            ),
        ]
        indexes = [
            models.Index(fields=("owner", "status")),
            models.Index(fields=("owner", "deadline")),
        ]

    def __str__(self):
        return self.name

    def progress(self):
        milestones = list(self.milestones.all())
        total = len(milestones)
        completed = sum(
            1
            for milestone in milestones
            if milestone.status == ProjectMilestone.Status.COMPLETED
        )
        return {
            "totalMilestones": total,
            "completedMilestones": completed,
            "available": total > 0,
            "percentage": (
                round((completed / total) * 100, 2) if total > 0 else None
            ),
        }


class ProjectMilestone(models.Model):
    """A project milestone whose completion drives project progress."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in-progress", "In progress"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="milestones",
    )
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True, default="")
    due_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    completion_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("due_date", "created_at")
        indexes = [models.Index(fields=("project", "due_date"))]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.status != self.Status.COMPLETED:
            self.completion_date = None
