import logging
import re
from decimal import Decimal
import smtplib
import socket
import time
from datetime import date

from django import forms
from django.conf import settings
from django.contrib.auth.forms import (
    BaseUserCreationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
    UserChangeForm as BaseUserChangeForm,
)
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sites.shortcuts import get_current_site
from django.core.mail import EmailMultiAlternatives
from django.template import loader
from django.urls import reverse

from allauth.socialaccount.forms import SignupForm as SocialSignupForm

from .models import (
    FINANCIAL_TRACKING_START_DATE,
    ZERO_MONEY,
    Account,
    BudgetAllocation,
    BudgetCategory,
    Goal,
    MonthlyBudget,
    PersonalShift,
    SavingsWithdrawal,
    Transaction,
    User,
    UserSupervisor,
)
from . import goals as goals_calc
from .profile_images import ProfileImageError, normalize_uploaded_image

logger = logging.getLogger(__name__)

# Connection-level failures happen before the message is submitted, so one
# retry cannot duplicate a delivered email. Errors raised after submission are
# deliberately not retried.
_CONNECTION_RETRY_ERRORS = (ConnectionRefusedError, socket.gaierror)
_CONNECTION_ATTEMPTS = 2
_CONNECTION_RETRY_DELAY_SECONDS = 1.0


def _smtp_target():
    options = settings.MAILERS.get("default", {}).get("OPTIONS", {}) or {}
    return options.get("host") or "", options.get("port") or ""


class GabswebPasswordResetForm(PasswordResetForm):
    google_recovery_subject_template = (
        "templates/emails/google_password_recovery_subject.txt"
    )
    google_recovery_text_template = (
        "templates/emails/google_password_recovery_email.txt"
    )
    google_recovery_html_template = (
        "templates/emails/google_password_recovery_email.html"
    )

    def _log_failure(self, context, exc, host, port, attempt, attempts):
        detail = f"type={type(exc).__name__}"
        if isinstance(exc, OSError) and getattr(exc, "errno", None) is not None:
            detail += f" errno={exc.errno}"
        if isinstance(exc, smtplib.SMTPRecipientsRefused):
            codes = sorted({code for code, _ in exc.recipients.values()})
            detail += (
                f" smtp_codes={codes}"
                f" recipients_refused={len(exc.recipients)}"
            )
        elif isinstance(exc, smtplib.SMTPResponseException):
            error = exc.smtp_error
            if isinstance(error, bytes):
                error = error.decode("utf-8", "replace")
            detail += f" smtp_code={exc.smtp_code} smtp_response={str(error)[:200]}"
        logger.error(
            "%s email delivery failed for user id %s "
            "(%s smtp_host=%s smtp_port=%s attempt=%s/%s).",
            context.get("email_kind", "Password reset"),
            context["user"].pk,
            detail,
            host,
            port,
            attempt,
            attempts,
        )

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        subject = "".join(
            loader.render_to_string(subject_template_name, context).splitlines()
        )
        body = loader.render_to_string(email_template_name, context)
        html = None
        if html_email_template_name is not None:
            html = loader.render_to_string(html_email_template_name, context)

        host, port = _smtp_target()
        for attempt in range(1, _CONNECTION_ATTEMPTS + 1):
            message = EmailMultiAlternatives(subject, body, from_email, [to_email])
            if html is not None:
                message.attach_alternative(html, "text/html")

            try:
                sent = message.send(fail_silently=False)
            except _CONNECTION_RETRY_ERRORS as exc:
                if attempt < _CONNECTION_ATTEMPTS:
                    logger.warning(
                        "%s email connection failed for user id %s "
                        "(type=%s smtp_host=%s smtp_port=%s attempt=%s/%s); "
                        "retrying once.",
                        context.get("email_kind", "Password reset"),
                        context["user"].pk,
                        type(exc).__name__,
                        host,
                        port,
                        attempt,
                        _CONNECTION_ATTEMPTS,
                    )
                    time.sleep(_CONNECTION_RETRY_DELAY_SECONDS)
                    continue
                self._log_failure(
                    context, exc, host, port, attempt, _CONNECTION_ATTEMPTS
                )
                return 0
            except Exception as exc:
                self._log_failure(
                    context, exc, host, port, attempt, _CONNECTION_ATTEMPTS
                )
                return 0

            if sent:
                logger.info(
                    "%s email accepted by the mail server for user id %s.",
                    context.get("email_kind", "Password reset"),
                    context["user"].pk,
                )
            else:
                logger.error(
                    "%s email was not submitted for user id %s "
                    "(backend returned 0).",
                    context.get("email_kind", "Password reset"),
                    context["user"].pk,
                )
            return sent

        return 0

    def save(
        self,
        domain_override=None,
        subject_template_name="registration/password_reset_subject.txt",
        email_template_name="registration/password_reset_email.html",
        use_https=False,
        token_generator=default_token_generator,
        from_email=None,
        request=None,
        html_email_template_name=None,
        extra_email_context=None,
    ):
        """Send a reset only to accounts with a usable local password.

        Google-only accounts have no Gabsweb password to reset. They receive a
        targeted, privacy-preserving email that explains Google sign-in
        instead. The public response never changes, so the flow does not reveal
        whether an address is registered or how it authenticates.
        """
        from allauth.socialaccount.models import SocialAccount

        email = self.cleaned_data["email"]
        if not domain_override:
            current_site = get_current_site(request)
            site_name = current_site.name
            domain = current_site.domain
        else:
            site_name = domain = domain_override

        active_users = list(
            User.objects.filter(email__iexact=email, is_active=True)
        )
        google_only_users = [
            user
            for user in active_users
            if not user.has_usable_password()
            and SocialAccount.objects.filter(
                user=user, provider="google"
            ).exists()
        ]
        if google_only_users:
            extra_context = dict(extra_email_context or {})
            base = (extra_context.get("public_base_url") or "").rstrip("/")
            origin = base or f"{'https' if use_https else 'http'}://{domain}"
            login_path = reverse("core:login")
            google_path = reverse("google_login")
            for user in google_only_users:
                context = {
                    "email": user.email,
                    "domain": domain,
                    "site_name": site_name,
                    "user": user,
                    "first_name": user.get_first_display_name(),
                    "protocol": "https" if use_https else "http",
                    "login_url": f"{origin}{login_path}",
                    "google_login_url": f"{origin}{google_path}",
                    "email_kind": "Google sign-in guidance",
                    **extra_context,
                }
                self.send_mail(
                    self.google_recovery_subject_template,
                    self.google_recovery_text_template,
                    context,
                    from_email,
                    user.email,
                    html_email_template_name=self.google_recovery_html_template,
                )

        if any(user.has_usable_password() for user in active_users):
            return super().save(
                domain_override=domain_override,
                subject_template_name=subject_template_name,
                email_template_name=email_template_name,
                use_https=use_https,
                token_generator=token_generator,
                from_email=from_email,
                request=request,
                html_email_template_name=html_email_template_name,
                extra_email_context=extra_email_context,
            )
        return None


class GabswebSetPasswordForm(SetPasswordForm):
    """Create a first local password for a Google-only account."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("new_password1", "new_password2"):
            self.fields[name].widget.attrs.update(
                {
                    "autocomplete": "new-password",
                    "placeholder": "At least 8 characters",
                }
            )


class GabswebPasswordChangeForm(PasswordChangeForm):
    """Change an existing local password without disturbing Google sign-in."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].widget.attrs.update(
            {"autocomplete": "current-password"}
        )
        for name in ("new_password1", "new_password2"):
            self.fields[name].widget.attrs.update(
                {
                    "autocomplete": "new-password",
                    "placeholder": "At least 8 characters",
                }
            )


class UserCreationForm(BaseUserCreationForm):
    class Meta:
        model = User
        fields = ("email",)
        field_classes = {}


class UserChangeForm(BaseUserChangeForm):
    class Meta:
        model = User
        fields = "__all__"
        field_classes = {}


class LoginForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(
            attrs={"placeholder": "Enter your email", "autocomplete": "email"}
        ),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"placeholder": "Enter your password", "autocomplete": "current-password"}
        ),
    )
    remember_me = forms.BooleanField(required=False)

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class ProfileUpdateForm(forms.Form):
    name = forms.CharField(max_length=150)
    email = forms.EmailField()
    professional_role = forms.CharField(
        max_length=80,
        required=False,
        error_messages={
            "max_length": "Professional role must be 80 characters or fewer."
        },
    )
    profile_image = forms.ImageField(required=False)
    remove_profile_image = forms.BooleanField(required=False)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if not name:
            raise forms.ValidationError("Enter your full name.")
        return name

    def clean_professional_role(self):
        return " ".join((self.cleaned_data.get("professional_role") or "").split())

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        existing = User.objects.filter(email__iexact=email)
        if self.user is not None:
            existing = existing.exclude(pk=self.user.pk)
        if existing.exists():
            raise forms.ValidationError(
                "An account with this email address already exists."
            )
        return email

    def clean_profile_image(self):
        image = self.cleaned_data.get("profile_image")
        if not image:
            return image
        try:
            normalize_uploaded_image(image)
        except ProfileImageError as exc:
            raise forms.ValidationError(str(exc)) from exc
        image.seek(0)
        return image


class RegistrationForm(BaseUserCreationForm):
    full_name = forms.CharField(
        label="Full name",
        max_length=150,
        widget=forms.TextInput(
            attrs={"placeholder": "Enter your full name", "autocomplete": "name"}
        ),
    )
    workplace_access = forms.ChoiceField(
        label="Do you work at GCTN or Apex Pay?",
        choices=(("no", "No"), ("yes", "Yes")),
        widget=forms.RadioSelect,
    )
    workplace_company = forms.ChoiceField(
        label="Which company?",
        choices=(("", "Not specified"),) + tuple(User.WorkplaceCompany.choices),
        required=False,
    )
    terms = forms.BooleanField(
        label="I agree to the terms and conditions.",
        error_messages={"required": "Agree to the terms before creating your account."},
    )

    class Meta:
        model = User
        fields = ("email",)
        field_classes = {}

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email address already exists.")
        return email

    def clean_full_name(self):
        name = " ".join(self.cleaned_data["full_name"].split())
        if not name:
            raise forms.ValidationError("Enter your full name.")
        return name

    def save(self, commit=True):
        user = super().save(commit=False)
        parts = self.cleaned_data["full_name"].split(maxsplit=1)
        user.first_name = parts[0]
        user.last_name = parts[1] if len(parts) > 1 else ""
        user.account_status = (
            User.AccountStatus.PENDING
            if self.cleaned_data["workplace_access"] == "yes"
            else User.AccountStatus.GENERAL
        )
        user.workplace_company = (
            self.cleaned_data.get("workplace_company") or ""
            if self.cleaned_data["workplace_access"] == "yes"
            else ""
        )
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
            self.save_m2m()
        return user


class GoogleSocialSignupForm(SocialSignupForm):
    workplace_access = forms.ChoiceField(
        label="Do you work at GCTN or Apex Pay?",
        choices=(("no", "No"), ("yes", "Yes")),
        widget=forms.RadioSelect,
    )
    workplace_company = forms.ChoiceField(
        label="Which company?",
        choices=(("", "Not specified"),) + tuple(User.WorkplaceCompany.choices),
        required=False,
    )
    terms = forms.BooleanField(
        label="I agree to the terms and conditions.",
        error_messages={"required": "Agree to the terms before creating your account."},
    )
    field_order = ("email", "workplace_access", "workplace_company", "terms")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "email" in self.fields:
            self.fields["email"].disabled = True

    def custom_signup(self, request, user):
        user.account_status = (
            User.AccountStatus.PENDING
            if self.cleaned_data["workplace_access"] == "yes"
            else User.AccountStatus.GENERAL
        )
        user.workplace_company = (
            self.cleaned_data.get("workplace_company") or ""
            if self.cleaned_data["workplace_access"] == "yes"
            else ""
        )
        user.is_staff = False
        user.is_superuser = False
        user.save(
            update_fields=[
                "account_status",
                "workplace_company",
                "is_staff",
                "is_superuser",
            ]
        )


class WorkProfileForm(forms.Form):
    work_team = forms.CharField(max_length=80, required=False)
    joining_date = forms.DateField(required=False)
    joining_arrangement = forms.CharField(max_length=120, required=False)
    works_shifts = forms.BooleanField(required=False)

    def clean_work_team(self):
        return " ".join((self.cleaned_data.get("work_team") or "").split())

    def clean_joining_arrangement(self):
        return " ".join(
            (self.cleaned_data.get("joining_arrangement") or "").split()
        )


class SupervisorForm(forms.ModelForm):
    class Meta:
        model = UserSupervisor
        fields = ("name",)

    def clean_name(self):
        name = " ".join((self.cleaned_data.get("name") or "").split())
        if not name:
            raise forms.ValidationError("Enter a supervisor name.")
        return name


class PersonalShiftForm(forms.ModelForm):
    class Meta:
        model = PersonalShift
        fields = ("name", "start_time", "end_time", "is_active")

    def clean_name(self):
        name = " ".join((self.cleaned_data.get("name") or "").split())
        if not name:
            raise forms.ValidationError("Enter a shift name.")
        return name

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if start and end and start == end:
            raise forms.ValidationError(
                {"end_time": "Shift start and end times must be different."}
            )
        return cleaned


class AccountForm(forms.ModelForm):
    opening_balance = forms.DecimalField(
        label="Opening balance",
        max_digits=14,
        decimal_places=2,
        min_value=ZERO_MONEY,
        required=True,
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "min": "0",
                "inputmode": "decimal",
                "placeholder": "0.00",
            }
        ),
        help_text=(
            "Money already held in this account before tracking began. "
            "This is not counted as income."
        ),
    )

    class Meta:
        model = Account
        fields = (
            "name",
            "account_type",
            "opening_balance",
            "opening_balance_date",
            "description",
        )
        widgets = {
            "name": forms.TextInput(
                attrs={"maxlength": 80, "placeholder": "e.g. Main bank account"}
            ),
            "opening_balance_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(
                attrs={
                    "rows": 2,
                    "maxlength": 240,
                    "placeholder": "Optional note about this account",
                }
            ),
        }

    def __init__(self, *args, owner=None, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)
        if not self.is_bound and not (self.instance and self.instance.pk):
            self.initial.setdefault(
                "opening_balance_date", FINANCIAL_TRACKING_START_DATE
            )

    def clean_name(self):
        name = " ".join((self.cleaned_data.get("name") or "").split())
        if not name:
            raise forms.ValidationError("Enter a name for this account.")
        if self.owner is not None:
            existing = Account.objects.filter(
                owner=self.owner, name__iexact=name
            )
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(
                    "You already have an account with this name."
                )
        return name

    def clean_opening_balance(self):
        balance = self.cleaned_data.get("opening_balance")
        if balance is None:
            raise forms.ValidationError("Enter the account's opening balance.")
        if balance < ZERO_MONEY:
            raise forms.ValidationError("Opening balance cannot be negative.")
        return balance.quantize(ZERO_MONEY)

    def save(self, commit=True):
        account = super().save(commit=False)
        if self.owner is not None:
            account.owner = self.owner
        if commit:
            account.full_clean()
            account.save()
            self.save_m2m()
        return account


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = (
            "transaction_type",
            "amount",
            "date",
            "account",
            "destination_account",
            "category",
            "description",
            "payment_method",
            "notes",
            "allocation",
            "goal",
            "client_reference",
        )
        widgets = {
            "amount": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal"}
            ),
            "date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.TextInput(attrs={"maxlength": 240}),
            "category": forms.TextInput(attrs={"maxlength": 80}),
            "payment_method": forms.TextInput(attrs={"maxlength": 40}),
            "notes": forms.Textarea(attrs={"rows": 3, "maxlength": 400}),
            "client_reference": forms.HiddenInput(),
        }

    def __init__(self, *args, owner=None, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)
        if owner is not None:
            self.instance.owner = owner
            self.fields["account"].queryset = Account.objects.filter(
                owner=owner
            ).order_by("created_at", "name")
            self.fields["destination_account"].queryset = Account.objects.filter(
                owner=owner
            ).order_by("created_at", "name")
            self.fields["allocation"].queryset = BudgetAllocation.objects.filter(
                budget__owner=owner
            ).select_related("budget", "category")
            self.fields["goal"].queryset = Goal.objects.filter(
                owner=owner
            ).order_by("created_at", "name")
        self.fields["destination_account"].required = False
        self.fields["allocation"].required = False
        self.fields["goal"].required = False

    def _validate_account(self, field_name, account):
        if account is None:
            return
        if self.owner is not None and account.owner_id != self.owner.pk:
            raise forms.ValidationError("Select one of your own accounts.")
        original_id = None
        if not self.instance._state.adding:
            original_id = getattr(self.instance, field_name + "_id", None)
        if (
            account.status == Account.Status.ARCHIVED
            and (self.instance._state.adding or account.pk != original_id)
        ):
            raise forms.ValidationError(
                "This account is archived and cannot receive new activity."
            )

    def clean_account(self):
        account = self.cleaned_data.get("account")
        if account is None:
            raise forms.ValidationError("Choose the account for this transaction.")
        self._validate_account("account", account)
        return account

    def clean_destination_account(self):
        account = self.cleaned_data.get("destination_account")
        if account is not None:
            self._validate_account("destination_account", account)
        return account

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is None:
            raise forms.ValidationError("Enter a transaction amount.")
        if amount <= ZERO_MONEY:
            raise forms.ValidationError(
                "Transaction amount must be greater than zero."
            )
        return amount.quantize(ZERO_MONEY)

    def clean_allocation(self):
        allocation = self.cleaned_data.get("allocation")
        if allocation is None:
            return allocation
        if self.owner is not None and allocation.budget.owner_id != self.owner.pk:
            raise forms.ValidationError(
                "Select one of your own budget allocations."
            )
        transaction_type = self.cleaned_data.get("transaction_type")
        if transaction_type and transaction_type != Transaction.TransactionType.EXPENSE:
            raise forms.ValidationError(
                "Only expense transactions can be linked to a budget allocation."
            )
        transaction_date = self.cleaned_data.get("date")
        if (
            transaction_date
            and allocation.budget.month != transaction_date.replace(day=1)
        ):
            raise forms.ValidationError(
                "The expense date must fall within the allocation's financial month."
            )
        return allocation

    def clean_goal(self):
        goal = self.cleaned_data.get("goal")
        if goal is None:
            return goal
        if self.owner is not None and goal.owner_id != self.owner.pk:
            raise forms.ValidationError("Select one of your own goals.")
        transaction_type = self.cleaned_data.get("transaction_type")
        if transaction_type and transaction_type != Transaction.TransactionType.EXPENSE:
            raise forms.ValidationError(
                "Only expense transactions can be linked to a goal."
            )
        if (
            not self.instance._state.adding
            and self.instance.goal_id == goal.pk
        ):
            return goal
        error = goals_calc.purchase_link_error(goal, self.owner)
        if error:
            raise forms.ValidationError(error)
        return goal

    def clean(self):
        cleaned = super().clean()
        transaction_type = cleaned.get("transaction_type")
        account = cleaned.get("account")
        destination = cleaned.get("destination_account")
        if transaction_type == Transaction.TransactionType.TRANSFER:
            if destination is None:
                self.add_error(
                    "destination_account", "Choose a destination account."
                )
            elif account is not None and destination.pk == account.pk:
                self.add_error(
                    "destination_account",
                    "Transfer accounts must be different.",
                )
            if cleaned.get("allocation") is not None:
                self.add_error(
                    "allocation",
                    "Only expense transactions can be linked to a budget allocation.",
                )
            if cleaned.get("goal") is not None:
                self.add_error(
                    "goal",
                    "Only expense transactions can be linked to a goal.",
                )
        else:
            if destination is not None:
                cleaned["destination_account"] = None
            if (
                transaction_type == Transaction.TransactionType.INCOME
                and cleaned.get("allocation") is not None
            ):
                self.add_error(
                    "allocation",
                    "Only expense transactions can be linked to a budget allocation.",
                )
            if (
                transaction_type == Transaction.TransactionType.INCOME
                and cleaned.get("goal") is not None
            ):
                self.add_error(
                    "goal",
                    "Only expense transactions can be linked to a goal.",
                )
        return cleaned

    def save(self, commit=True):
        entry = super().save(commit=False)
        if self.owner is not None:
            entry.owner = self.owner
        if commit:
            entry.full_clean()
            entry.save()
            self.save_m2m()
        return entry


class BudgetAllocationForm(forms.Form):
    month = forms.CharField(
        max_length=7,
        error_messages={"required": "Select a valid budget month."},
    )
    category = forms.CharField(
        max_length=80,
        error_messages={"required": "Choose or enter a valid allocation category."},
    )
    name = forms.CharField(
        max_length=80,
        required=False,
        error_messages={"max_length": "Allocation name must be 80 characters or fewer."},
    )
    planned_amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=ZERO_MONEY,
        max_value=Decimal("999999999999.99"),
        required=True,
        error_messages={
            "required": "Enter an allocation amount.",
            "invalid": "Enter a valid allocation amount.",
            "max_digits": "Enter a valid allocation amount.",
            "max_value": "The allocation amount is too large.",
            "min_value": "Enter an allocation amount greater than zero.",
        },
        widget=forms.NumberInput(
            attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal"}
        ),
    )
    status = forms.ChoiceField(
        choices=BudgetAllocation.Status.choices,
        error_messages={
            "required": "Choose a valid allocation status.",
            "invalid_choice": "Choose a valid allocation status.",
        },
    )
    description = forms.CharField(
        max_length=300,
        required=False,
        error_messages={"max_length": "Description must be 300 characters or fewer."},
    )

    def __init__(self, *args, owner=None, instance=None, **kwargs):
        self.owner = owner
        self.instance = instance
        super().__init__(*args, **kwargs)
        if instance is not None and not self.is_bound:
            self.initial.update(
                {
                    "month": instance.budget.month_key,
                    "category": instance.category.name,
                    "name": instance.name,
                    "planned_amount": instance.planned_amount,
                    "status": instance.status,
                    "description": instance.description,
                }
            )

    def clean_month(self):
        value = (self.cleaned_data.get("month") or "").strip()
        if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", value):
            raise forms.ValidationError("Select a valid budget month.")
        year, month = (int(part) for part in value.split("-"))
        return date(year, month, 1)

    def clean_category(self):
        name = " ".join((self.cleaned_data.get("category") or "").split())
        if not name:
            raise forms.ValidationError(
                "Choose or enter a valid allocation category."
            )
        if self.owner is None:
            return name
        category = BudgetCategory.objects.filter(
            owner=self.owner, name__iexact=name
        ).first()
        if category is None:
            category = BudgetCategory(owner=self.owner, name=name)
            category.full_clean()
            category.save()
        return category

    def clean_name(self):
        return " ".join((self.cleaned_data.get("name") or "").split())

    def clean_planned_amount(self):
        amount = self.cleaned_data.get("planned_amount")
        if amount is None:
            raise forms.ValidationError("Enter the planned allocation amount.")
        if amount <= ZERO_MONEY:
            raise forms.ValidationError(
                "Enter an allocation amount greater than zero."
            )
        return amount.quantize(ZERO_MONEY)

    def save(self):
        month = self.cleaned_data["month"]
        category = self.cleaned_data["category"]
        budget, _created = MonthlyBudget.objects.get_or_create(
            owner=self.owner, month=month
        )
        allocation = self.instance or BudgetAllocation(budget=budget)
        allocation.budget = budget
        allocation.category = category
        allocation.name = self.cleaned_data.get("name") or category.name
        allocation.planned_amount = self.cleaned_data["planned_amount"]
        allocation.status = self.cleaned_data["status"]
        allocation.description = self.cleaned_data.get("description") or ""
        allocation.full_clean()
        allocation.save()
        return allocation


class SavingsWithdrawalForm(forms.ModelForm):
    class Meta:
        model = SavingsWithdrawal
        fields = ("amount", "date", "reason")
        widgets = {
            "amount": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal"}
            ),
            "date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(
                attrs={
                    "rows": 3,
                    "maxlength": 300,
                    "placeholder": "e.g. Laptop repair",
                }
            ),
        }

    def __init__(self, *args, owner=None, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)
        self.fields["date"].required = True
        self.fields["reason"].required = True

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is None:
            raise forms.ValidationError("Enter the amount you withdrew.")
        if amount <= ZERO_MONEY:
            raise forms.ValidationError(
                "Withdrawal amount must be greater than zero."
            )
        return amount.quantize(ZERO_MONEY)

    def clean_reason(self):
        reason = " ".join((self.cleaned_data.get("reason") or "").split())
        if not reason:
            raise forms.ValidationError(
                "Enter what you used the withdrawn money for."
            )
        if len(reason) > 300:
            raise forms.ValidationError(
                "Reason must be 300 characters or fewer."
            )
        return reason

    def save(self, commit=True):
        withdrawal = super().save(commit=False)
        if self.owner is not None:
            withdrawal.owner = self.owner
        if commit:
            withdrawal.full_clean()
            withdrawal.save()
        return withdrawal


class GoalForm(forms.ModelForm):
    class Meta:
        model = Goal
        fields = ("name", "target_amount", "target_date", "description")
        widgets = {
            "name": forms.TextInput(
                attrs={"maxlength": 80, "placeholder": "e.g. Buy a laptop"}
            ),
            "target_amount": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal"}
            ),
            "target_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(
                attrs={
                    "rows": 3,
                    "maxlength": 300,
                    "placeholder": "What is this goal for?",
                }
            ),
        }

    def __init__(self, *args, owner=None, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)
        self.fields["target_date"].required = False
        self.fields["description"].required = False

    def clean_name(self):
        name = " ".join((self.cleaned_data.get("name") or "").split())
        if not name:
            raise forms.ValidationError("Enter a goal name.")
        return name

    def clean_target_amount(self):
        amount = self.cleaned_data.get("target_amount")
        if amount is None:
            raise forms.ValidationError("Enter a target amount.")
        if amount <= ZERO_MONEY:
            raise forms.ValidationError(
                "Target amount must be greater than zero."
            )
        return amount.quantize(ZERO_MONEY)

    def clean_description(self):
        return " ".join((self.cleaned_data.get("description") or "").split())

    def save(self, commit=True):
        goal = super().save(commit=False)
        if self.owner is not None:
            goal.owner = self.owner
        if commit:
            goal.full_clean()
            goal.save()
        return goal


class GoalAssignmentForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=ZERO_MONEY,
        max_value=Decimal("999999999999.99"),
        required=True,
        error_messages={
            "required": "Enter an amount to assign.",
            "invalid": "Enter a valid amount.",
        },
        widget=forms.NumberInput(
            attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal"}
        ),
    )

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is None:
            raise forms.ValidationError("Enter an amount to assign.")
        if amount <= ZERO_MONEY:
            raise forms.ValidationError(
                "Assignment amount must be greater than zero."
            )
        return amount.quantize(ZERO_MONEY)


class BudgetCategoryForm(forms.ModelForm):
    class Meta:
        model = BudgetCategory
        fields = ("name",)
        widgets = {"name": forms.TextInput(attrs={"maxlength": 80})}

    def __init__(self, *args, owner=None, instance=None, **kwargs):
        self.owner = owner
        super().__init__(*args, instance=instance, **kwargs)

    def clean_name(self):
        name = " ".join((self.cleaned_data.get("name") or "").split())
        if not name:
            raise forms.ValidationError("Enter a category name.")
        if self.owner is not None:
            existing = BudgetCategory.objects.filter(
                owner=self.owner, name__iexact=name
            )
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(
                    "You already have a category with this name."
                )
        return name

    def save(self, commit=True):
        category = super().save(commit=False)
        if self.owner is not None:
            category.owner = self.owner
        if commit:
            category.full_clean()
            category.save()
        return category
