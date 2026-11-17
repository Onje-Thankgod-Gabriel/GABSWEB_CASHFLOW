from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .admin_ui.branding import apply_admin_logout_redirect
from .admin_ui.pending import PendingVerificationsMixin
from .forms import UserChangeForm, UserCreationForm
from .models import (
    Account,
    BudgetAllocation,
    BudgetCategory,
    MonthlyBudget,
    Notification,
    PersonalShift,
    SavingsWithdrawal,
    Transaction,
    User,
    UserSupervisor,
    WorkRotationConfig,
    WorkScheduleRecord,
)


apply_admin_logout_redirect(admin.site)


@admin.register(User)
class UserAdmin(PendingVerificationsMixin, BaseUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_list_template = "admin/core/user/change_list.html"

    ordering = ("email",)
    list_display = (
        "email",
        "first_name",
        "last_name",
        "account_status",
        "workplace_company",
        "is_active",
        "is_staff",
        "is_superuser",
    )
    list_filter = (
        "account_status",
        "workplace_company",
        "is_active",
        "is_staff",
        "is_superuser",
        "groups",
    )
    search_fields = ("email", "first_name", "last_name")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Personal info",
            {"fields": ("first_name", "last_name", "professional_role")},
        ),
        (
            "Workplace access",
            {
                "fields": (
                    "account_status",
                    "workplace_company",
                    "work_team",
                    "joining_date",
                    "joining_arrangement",
                    "works_shifts",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "account_status",
                    "workplace_company",
                ),
            },
        ),
    )
    actions = ("approve_membership",)

    @admin.action(description="Approve selected GCTN/Apex Pay members")
    def approve_membership(self, request, queryset):
        return self.process_pending_bulk_action(request, queryset)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "owner",
        "account_type",
        "opening_balance",
        "opening_balance_date",
        "status",
    )
    list_filter = ("account_type", "status")
    search_fields = ("name", "owner__email")
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "transaction_type",
        "amount",
        "account",
        "destination_account",
        "owner",
    )
    list_filter = ("transaction_type", "date")
    search_fields = ("description", "category", "owner__email")
    autocomplete_fields = (
        "owner",
        "account",
        "destination_account",
        "allocation",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(BudgetCategory)
class BudgetCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    search_fields = ("name", "owner__email")
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(MonthlyBudget)
class MonthlyBudgetAdmin(admin.ModelAdmin):
    list_display = ("month", "owner", "created_at")
    list_filter = ("month",)
    search_fields = ("owner__email",)
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(BudgetAllocation)
class BudgetAllocationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "budget",
        "category",
        "planned_amount",
        "status",
    )
    list_filter = ("status", "budget__month")
    search_fields = ("name", "category__name", "budget__owner__email")
    autocomplete_fields = ("budget", "category")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SavingsWithdrawal)
class SavingsWithdrawalAdmin(admin.ModelAdmin):
    list_display = ("date", "amount", "reason", "owner")
    list_filter = ("date",)
    search_fields = ("reason", "owner__email")
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "event_type",
        "is_read",
        "email_status",
        "created_at",
    )
    list_filter = ("event_type", "is_read", "email_status")
    search_fields = ("title", "message", "owner__email")
    autocomplete_fields = ("owner",)
    readonly_fields = (
        "created_at",
        "read_at",
        "email_attempts",
        "email_sent_at",
        "email_last_error",
    )


@admin.register(UserSupervisor)
class UserSupervisorAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    search_fields = ("name", "owner__email")
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(PersonalShift)
class PersonalShiftAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "start_time", "end_time", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "owner__email")
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(WorkScheduleRecord)
class WorkScheduleRecordAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "owner",
        "shift_type",
        "status",
        "source",
        "is_exception",
        "updated_at",
    )
    list_filter = ("shift_type", "status", "source", "is_exception")
    search_fields = ("owner__email", "team", "notes")
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(WorkRotationConfig)
class WorkRotationConfigAdmin(admin.ModelAdmin):
    list_display = (
        "owner",
        "configured",
        "anchor_date",
        "anchor_shift",
        "anchor_position",
        "updated_at",
    )
    list_filter = ("configured",)
    search_fields = ("owner__email",)
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")
