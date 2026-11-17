from django.contrib import messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path

from ..models import User


def can_manage_pending(user):
    return bool(user and user.is_active and user.is_superuser)


def pending_applicants():
    return User.objects.filter(
        account_status=User.AccountStatus.PENDING,
        is_staff=False,
        is_superuser=False,
    )


def is_pending_applicant(user):
    return bool(
        user
        and user.account_status == User.AccountStatus.PENDING
        and not user.is_staff
        and not user.is_superuser
    )


def has_valid_workplace(user):
    return bool(user and user.workplace_company in User.WorkplaceCompany.values)


def _log_admin_action(actor, target, message):
    LogEntry.objects.log_actions(
        actor.pk,
        [target],
        CHANGE,
        message,
        single_object=True,
    )


def approve_pending_member(target, actor):
    target.account_status = User.AccountStatus.APPROVED
    User.objects.filter(pk=target.pk).update(
        account_status=User.AccountStatus.APPROVED
    )
    _log_admin_action(
        actor,
        target,
        (
            f"Approved workplace access for {target.email} "
            f"({target.workplace_company})."
        ),
    )


def return_pending_member(target, actor):
    target.account_status = User.AccountStatus.GENERAL
    User.objects.filter(pk=target.pk).update(
        account_status=User.AccountStatus.GENERAL
    )
    _log_admin_action(
        actor,
        target,
        (
            f"Returned {target.email} to General. Workplace data preserved "
            f"({target.workplace_company or 'none recorded'})."
        ),
    )


class PendingVerificationsMixin:
    """Superuser-only pending membership workflow for the native UserAdmin."""

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "pending/",
                self.admin_site.admin_view(self.pending_verifications_view),
                name="core_user_pending",
            ),
            path(
                "pending/<int:object_id>/approve/",
                self.admin_site.admin_view(self.approve_pending_view),
                name="core_user_approve_pending",
            ),
            path(
                "pending/<int:object_id>/return/",
                self.admin_site.admin_view(self.return_pending_view),
                name="core_user_return_pending",
            ),
        ]
        return custom_urls + urls

    def _require_pending_manager(self, request):
        if not can_manage_pending(request.user):
            raise PermissionDenied

    def _pending_context(self, request, **extra):
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "workplace_choices": User.WorkplaceCompany.choices,
        }
        context.update(extra)
        return context

    def pending_verifications_view(self, request):
        self._require_pending_manager(request)

        queryset = pending_applicants().filter(
            workplace_company__in=User.WorkplaceCompany.values
        )
        query = request.GET.get("q", "").strip()
        workplace = request.GET.get("workplace", "").strip()

        if query:
            queryset = queryset.filter(
                Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(professional_role__icontains=query)
                | Q(workplace_company__icontains=query)
            )
        if workplace in User.WorkplaceCompany.values:
            queryset = queryset.filter(workplace_company=workplace)

        queryset = queryset.order_by("date_joined", "email")
        paginator = Paginator(queryset, 20)
        page_obj = paginator.get_page(request.GET.get("page"))

        return TemplateResponse(
            request,
            "admin/core/user/pending_verifications.html",
            self._pending_context(
                request,
                title="Pending verifications",
                page_obj=page_obj,
                pending_total=paginator.count,
                query=query,
                selected_workplace=workplace,
            ),
        )

    def approve_pending_view(self, request, object_id):
        self._require_pending_manager(request)
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        try:
            with transaction.atomic():
                target = User.objects.select_for_update().get(pk=object_id)
                if not is_pending_applicant(target):
                    messages.error(
                        request,
                        "This member is no longer a pending applicant.",
                    )
                    return redirect("admin:core_user_pending")
                if not has_valid_workplace(target):
                    messages.error(
                        request,
                        "A valid GCTN or Apex Pay workplace is required before approval.",
                    )
                    return redirect("admin:core_user_pending")

                approve_pending_member(target, request.user)
        except User.DoesNotExist as exc:
            raise Http404 from exc

        messages.success(
            request,
            f"{target.email} was approved for workplace access.",
        )
        return redirect("admin:core_user_pending")

    def return_pending_view(self, request, object_id):
        self._require_pending_manager(request)
        target = get_object_or_404(pending_applicants(), pk=object_id)

        if request.method == "POST":
            try:
                with transaction.atomic():
                    target = User.objects.select_for_update().get(pk=object_id)
                    if not is_pending_applicant(target):
                        messages.error(
                            request,
                            "This member is no longer a pending applicant.",
                        )
                        return redirect("admin:core_user_pending")

                    return_pending_member(target, request.user)
            except User.DoesNotExist as exc:
                raise Http404 from exc

            messages.success(
                request,
                f"{target.email} was returned to General. Workplace data was preserved.",
            )
            return redirect("admin:core_user_pending")

        if request.method != "GET":
            return HttpResponseNotAllowed(["GET", "POST"])

        return TemplateResponse(
            request,
            "admin/core/user/pending_confirm_return.html",
            self._pending_context(
                request,
                title="Return to General",
                pending_user=target,
            ),
        )

    def process_pending_bulk_action(self, request, queryset):
        if not can_manage_pending(request.user):
            self.message_user(
                request,
                "Only the platform superuser can approve pending members.",
                level=messages.ERROR,
            )
            return

        selected_ids = list(queryset.values_list("pk", flat=True))
        if not selected_ids:
            self.message_user(
                request,
                "No users were selected.",
                level=messages.WARNING,
            )
            return

        approved = 0
        invalid_workplace = 0
        skipped = 0

        with transaction.atomic():
            for object_id in selected_ids:
                target = (
                    User.objects.select_for_update()
                    .filter(pk=object_id)
                    .first()
                )
                if target is None or not is_pending_applicant(target):
                    skipped += 1
                    continue
                if not has_valid_workplace(target):
                    invalid_workplace += 1
                    continue

                approve_pending_member(target, request.user)
                approved += 1

        if approved:
            self.message_user(
                request,
                f"{approved} membership application"
                f"{'' if approved == 1 else 's'} approved.",
                level=messages.SUCCESS,
            )
        if invalid_workplace:
            self.message_user(
                request,
                f"{invalid_workplace} selected user"
                f"{'' if invalid_workplace == 1 else 's'} had no valid "
                "GCTN or Apex Pay workplace and were not approved.",
                level=messages.WARNING,
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} selected user"
                f"{'' if skipped == 1 else 's'} were not eligible pending "
                "applicants and were skipped.",
                level=messages.WARNING,
            )
        if not approved and not invalid_workplace and not skipped:
            self.message_user(
                request,
                "No users were selected.",
                level=messages.WARNING,
            )
