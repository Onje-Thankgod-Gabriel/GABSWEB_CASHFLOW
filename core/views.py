import json
import logging
import mimetypes
import uuid
from datetime import date, time
from decimal import Decimal

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import get_hashers, identify_hasher
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core.cache import cache
from django.db import IntegrityError, transaction as db_transaction
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_http_methods

from allauth.socialaccount.providers.google.views import (
    oauth2_callback,
    oauth2_login,
)

from . import financials
from . import goals as goals_calc
from . import notifications as notifications_service
from . import reports as reports_service
from . import savings as savings_calc
from . import work_schedule as schedule_engine
from .report_pdf import build_report_pdf
from .forms import (
    AccountForm,
    BudgetAllocationForm,
    BudgetCategoryForm,
    GabswebPasswordResetForm,
    GoalAssignmentForm,
    GoalForm,
    LoginForm,
    PersonalShiftForm,
    ProfileUpdateForm,
    RegistrationForm,
    SavingsWithdrawalForm,
    SupervisorForm,
    TransactionForm,
    WorkProfileForm,
)
from .models import (
    ZERO_MONEY,
    Account,
    BudgetAllocation,
    BudgetCategory,
    Goal,
    GoalAssignment,
    MonthlyBudget,
    Notification,
    PersonalShift,
    SavingsWithdrawal,
    Transaction,
    UserSupervisor,
    WorkRotationConfig,
    WorkScheduleRecord,
)
from .profile_images import (
    ProfileImageError,
    remove_profile_images,
    save_manual_profile_image,
)

logger = logging.getLogger(__name__)


def _upgrade_password_hash(user, password):
    preferred = get_hashers()[0]
    if user.password and identify_hasher(user.password).algorithm != preferred.algorithm:
        user.set_password(password)
        user.save(update_fields=["password"])


def _safe_next_url(request, fallback):
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback


@login_required
def index(request):
    return render(request, 'index.html')


def bad_request(request):
    return render(request, '400.html')


def permission_denied(request):
    return render(request, '403.html')


def page_not_found(request):
    return render(request, '404.html')


def server_error(request):
    return render(request, '500.html')


@login_required
def budgets(request):
    return render(request, 'budgets.html')


@login_required
def goals(request):
    return render(request, 'goals.html')


@login_required
def insights(request):
    return render(request, 'insights.html')


def _report_period(request):
    mode = (request.GET.get("mode") or "month").strip().lower()
    if mode not in ("month", "year"):
        return None, None
    period = (request.GET.get("period") or "").strip()
    if not period:
        today = date.today()
        period = today.strftime("%Y-%m") if mode == "month" else str(today.year)
    bounds = reports_service.period_bounds(mode, period)
    if bounds is None:
        return None, None
    return mode, period


@login_required
@never_cache
@require_http_methods(["GET"])
def insights_api(request):
    mode, period = _report_period(request)
    if mode is None:
        return JsonResponse(
            {
                "ok": False,
                "errors": {"period": ["Use a valid monthly or yearly period."]},
            },
            status=400,
        )
    report = reports_service.build_period_report(request.user, mode, period)
    if report is None:
        return JsonResponse(
            {
                "ok": False,
                "errors": {"period": ["Use a valid monthly or yearly period."]},
            },
            status=400,
        )
    response = JsonResponse({"ok": True, "report": report})
    response["Cache-Control"] = "no-store"
    return response


@login_required
@never_cache
@require_http_methods(["GET"])
def insights_pdf(request):
    mode, period = _report_period(request)
    if mode is None:
        return HttpResponseBadRequest("Use a valid monthly or yearly period.")
    report = reports_service.build_period_report(request.user, mode, period)
    if report is None:
        return HttpResponseBadRequest("Use a valid monthly or yearly period.")
    pdf = build_report_pdf(report)
    response = HttpResponse(pdf, content_type="application/pdf")
    report_type = "Yearly" if mode == "year" else "Monthly"
    response["Content-Disposition"] = (
        f'attachment; filename="Gabsweb-{report_type}-Report-'
        f'{date.today().isoformat()}.pdf"'
    )
    response["Cache-Control"] = "no-store"
    return response


def login(request):
    if request.user.is_authenticated:
        return redirect("core:index")

    form = LoginForm(request.POST or None)
    next_url = request.POST.get("next") or request.GET.get("next") or ""

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        password = form.cleaned_data["password"]
        user = authenticate(
            request,
            email=email,
            password=password,
        )
        if user is not None:
            _upgrade_password_hash(user, password)
            auth_login(request, user)
            if form.cleaned_data.get("remember_me"):
                request.session.set_expiry(django_settings.SESSION_COOKIE_AGE)
            else:
                request.session.set_expiry(0)
            return redirect(_safe_next_url(request, "core:index"))
        
        from .models import User as UserModel

        existing_user = UserModel.objects.filter(email=email.strip().lower()).first()
        if existing_user is None:
            form.add_error(
                None,
                "This email is not registered. Please check your email address or create an account.",
            )
        elif not existing_user.is_active:
            form.add_error(
                None,
                "This account is inactive. Contact the platform owner if you need access.",
            )
        else:
            form.add_error(None, "The password you entered is incorrect.")

    return render(request, "login.html", {"form": form, "next": next_url})


@require_POST
@login_required
def logout(request):
    auth_logout(request)
    messages.success(request, "You have been signed out.")
    return redirect("core:login")


class GabswebPasswordResetView(PasswordResetView):
    form_class = GabswebPasswordResetForm
    template_name = "password-reset.html"
    email_template_name = "templates/emails/password_reset_email.txt"
    html_email_template_name = "templates/emails/password_reset_email.html"
    subject_template_name = "templates/emails/password_reset_subject.txt"
    success_url = reverse_lazy("core:password_reset_done")
    throttle_ip_limit = 10
    throttle_email_limit = 5
    throttle_window = 900

    def _hit_limit(self, key, limit):
        added = cache.add(key, 1, self.throttle_window)
        if added:
            return False
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, self.throttle_window)
            return False
        return count > limit

    def _is_throttled(self, request):
        if request.method != "POST":
            return False
        client_ip = request.META.get("REMOTE_ADDR") or "unknown"
        if self._hit_limit(f"pwreset:ip:{client_ip}", self.throttle_ip_limit):
            logger.warning("Password reset rate limit reached for client address.")
            return True
        email = (request.POST.get("email") or "").strip().lower()
        if email and self._hit_limit(
            f"pwreset:email:{email}", self.throttle_email_limit
        ):
            logger.warning("Password reset rate limit reached for an email request.")
            return True
        return False

    def get_extra_email_context(self):
        return {
            "public_base_url": django_settings.PASSWORD_RESET_BASE_URL,
            "expiry_hours": max(1, django_settings.PASSWORD_RESET_TIMEOUT // 3600),
            "email_logo_url": notifications_service.email_logo_url(),
        }

    def form_valid(self, form):
        self.extra_email_context = self.get_extra_email_context()
        return super().form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if self._is_throttled(request):
            return redirect("core:password_reset_done")
        return super().dispatch(request, *args, **kwargs)


class GabswebPasswordResetDoneView(PasswordResetDoneView):
    template_name = "password-reset-done.html"


class GabswebPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "password-reset-confirm.html"
    success_url = reverse_lazy("core:password_reset_complete")


class GabswebPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "password-reset-complete.html"


def password_reset_confirm(request):
    return render(request, "password-reset-confirm.html", {"validlink": False})


@login_required
def planner(request):
    return render(request, 'planner.html')


def privacy_policy(request):
    return render(request, 'privacy-policy.html')


def register(request):
    if request.user.is_authenticated:
        return redirect("core:index")

    form = RegistrationForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = form.save()
        request.session["suppress_login_notification"] = True
        auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        try:
            notifications_service.handle_new_registration(user)
        except Exception:  # noqa: BLE001 - registration must still succeed
            logger.exception(
                "Could not create welcome notification for user id %s.", user.pk
            )
        if user.account_status != user.AccountStatus.PENDING:
            messages.success(request, "Your account was created and you are now signed in.")
        return redirect("core:index")

    return render(request, "register.html", {"form": form})


@login_required
def savings(request):
    return render(request, 'savings.html')


@login_required
def settings(request):
    from allauth.socialaccount.models import SocialAccount

    google_account = SocialAccount.objects.filter(
        user=request.user, provider="google"
    ).first()
    return render(
        request,
        'settings.html',
        {'google_account': google_account},
    )


def _google_oauth_unavailable(request):
    if getattr(django_settings, "GOOGLE_OAUTH_CONFIGURED", False):
        return None
    messages.error(
        request,
        "Google sign-in is not configured for this environment. "
        "Use your email and password instead.",
    )
    return redirect("core:login")


def google_login(request):
    unavailable = _google_oauth_unavailable(request)
    if unavailable:
        return unavailable
    return oauth2_login(request)


def google_callback(request):
    unavailable = _google_oauth_unavailable(request)
    if unavailable:
        return unavailable
    return oauth2_callback(request)


@require_POST
@login_required
def google_connect(request):
    from allauth.socialaccount.models import SocialAccount

    if SocialAccount.objects.filter(user=request.user, provider="google").exists():
        messages.info(request, "A Google account is already connected to your profile.")
        return redirect("core:settings")

    return redirect(f"{reverse('google_login')}?process=connect")


def _profile_payload(user):
    return {
        "ok": True,
        "full_name": user.get_full_name(),
        "first_name": user.get_first_display_name(),
        "two_part_name": user.get_two_part_name(),
        "email": user.email,
        "professional_role": user.professional_role,
        "profile_image_url": user.effective_profile_image_url_versioned,
        "uses_google_profile_image": user.uses_google_profile_image,
    }


@login_required
@require_http_methods(["GET"])
def profile_api(request):
    """Return the authenticated user's own profile identity."""
    response = JsonResponse(_profile_payload(request.user))
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Vary"] = "Cookie"
    return response


@require_POST
@login_required
def update_profile(request):
    form = ProfileUpdateForm(request.POST, request.FILES, user=request.user)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": form.errors.get_json_data()},
            status=400,
        )

    with db_transaction.atomic():
        user = _lock_owner(request.user)
        if form.cleaned_data.get("remove_profile_image"):
            remove_profile_images(user)
        elif form.cleaned_data.get("profile_image"):
            try:
                save_manual_profile_image(
                    user, form.cleaned_data["profile_image"]
                )
            except ProfileImageError as exc:
                return JsonResponse(
                    {
                        "ok": False,
                        "errors": {
                            "profile_image": [
                                {"message": str(exc), "code": "invalid"}
                            ]
                        },
                    },
                    status=400,
                )

        name = form.cleaned_data["name"]
        parts = name.split(maxsplit=1)
        user.first_name = parts[0]
        user.last_name = parts[1] if len(parts) > 1 else ""
        user.email = form.cleaned_data["email"]
        user.professional_role = form.cleaned_data["professional_role"]
        user.save(
            update_fields=[
                "first_name",
                "last_name",
                "email",
                "professional_role",
            ]
        )

    return JsonResponse(_profile_payload(user))


@login_required
def profile_image(request):
    image = request.user.effective_profile_image
    if not image:
        raise Http404
    try:
        image_file = image.storage.open(image.name, "rb")
    except (OSError, ValueError):
        raise Http404
    content_type = mimetypes.guess_type(image.name)[0]
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        image_file.close()
        raise Http404
    response = FileResponse(image_file, content_type=content_type)
    response["Cache-Control"] = "private, no-store, max-age=0, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Vary"] = "Cookie"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def profile_media_denied(request, path=None):
    raise Http404


def _supervisor_payload(supervisor):
    return {
        "id": str(supervisor.pk),
        "name": supervisor.name,
    }


def _personal_shift_payload(shift):
    return {
        "id": str(shift.pk),
        "name": shift.name,
        "startTime": shift.start_time.strftime("%H:%M"),
        "endTime": shift.end_time.strftime("%H:%M"),
        "isActive": shift.is_active,
    }


def _work_profile_payload(user):
    approved = user.account_status == user.AccountStatus.APPROVED
    return {
        "workTeam": user.work_team,
        "joiningDate": user.joining_date.isoformat() if user.joining_date else "",
        "joiningArrangement": user.joining_arrangement,
        "worksShifts": user.works_shifts,
        "accountStatus": user.account_status,
        "statusLabel": user.get_account_status_display(),
        "workplaceCompany": user.workplace_company,
        "workplaceLabel": user.workplace_label,
        "hasApprovedAccess": approved,
        "isPending": user.account_status == user.AccountStatus.PENDING,
        "isGeneral": user.account_status == user.AccountStatus.GENERAL,
        "supervisors": [
            _supervisor_payload(supervisor)
            for supervisor in user.supervisors.all()
        ],
        "shifts": [
            _personal_shift_payload(shift) for shift in user.personal_shifts.all()
        ],
    }


@login_required
@require_http_methods(["GET", "POST"])
def work_profile_api(request):
    if request.method == "GET":
        return JsonResponse(
            {"ok": True, "workProfile": _work_profile_payload(request.user)}
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = WorkProfileForm(data)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )

    if request.user.account_status != request.user.AccountStatus.APPROVED:
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "__all__": [
                        "Workplace work details are only available to approved "
                        "GCTN or Apex Pay members."
                    ]
                },
            },
            status=403,
        )

    with db_transaction.atomic():
        user = _lock_owner(request.user)
        user.work_team = form.cleaned_data.get("work_team") or ""
        user.joining_date = form.cleaned_data.get("joining_date")
        user.joining_arrangement = (
            form.cleaned_data.get("joining_arrangement") or ""
        )
        user.works_shifts = bool(form.cleaned_data.get("works_shifts"))
        user.save(
            update_fields=[
                "work_team",
                "joining_date",
                "joining_arrangement",
                "works_shifts",
            ]
        )

    return JsonResponse(
        {"ok": True, "workProfile": _work_profile_payload(user)}
    )


@login_required
@require_http_methods(["GET", "POST"])
def supervisors_api(request):
    if request.user.account_status != request.user.AccountStatus.APPROVED:
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "__all__": [
                        "Supervisors are only available to approved GCTN or "
                        "Apex Pay members."
                    ]
                },
            },
            status=403,
        )
    if request.method == "GET":
        return JsonResponse(
            {
                "ok": True,
                "supervisors": [
                    _supervisor_payload(supervisor)
                    for supervisor in request.user.supervisors.all()
                ],
            }
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = SupervisorForm(data)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    name = form.cleaned_data["name"]
    if UserSupervisor.objects.filter(
        owner=request.user, name__iexact=name
    ).exists():
        return JsonResponse(
            {
                "ok": False,
                "errors": {"name": ["This supervisor has already been added."]},
            },
            status=400,
        )
    supervisor = UserSupervisor.objects.create(owner=request.user, name=name)
    return JsonResponse(
        {"ok": True, "supervisor": _supervisor_payload(supervisor)}, status=201
    )


@login_required
@require_http_methods(["POST", "DELETE"])
def supervisor_detail(request, pk):
    if request.user.account_status != request.user.AccountStatus.APPROVED:
        return JsonResponse({"ok": False}, status=403)
    supervisor = get_object_or_404(
        UserSupervisor, pk=pk, owner=request.user
    )
    if request.method == "DELETE":
        supervisor.delete()
        return JsonResponse({"ok": True, "deleted": str(pk)})

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = SupervisorForm(data, instance=supervisor)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    name = form.cleaned_data["name"]
    if (
        UserSupervisor.objects.filter(owner=request.user, name__iexact=name)
        .exclude(pk=supervisor.pk)
        .exists()
    ):
        return JsonResponse(
            {
                "ok": False,
                "errors": {"name": ["This supervisor has already been added."]},
            },
            status=400,
        )
    supervisor.name = name
    supervisor.save(update_fields=["name", "updated_at"])
    return JsonResponse(
        {"ok": True, "supervisor": _supervisor_payload(supervisor)}
    )


@login_required
@require_http_methods(["GET", "POST"])
def personal_shifts_api(request):
    if request.method == "GET":
        return JsonResponse(
            {
                "ok": True,
                "shifts": [
                    _personal_shift_payload(shift)
                    for shift in request.user.personal_shifts.all()
                ],
            }
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = PersonalShiftForm(data)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    name = form.cleaned_data["name"]
    if PersonalShift.objects.filter(
        owner=request.user, name__iexact=name
    ).exists():
        return JsonResponse(
            {
                "ok": False,
                "errors": {"name": ["A shift with this name already exists."]},
            },
            status=400,
        )
    shift = form.save(commit=False)
    shift.owner = request.user
    shift.full_clean()
    shift.save()
    return JsonResponse(
        {"ok": True, "shift": _personal_shift_payload(shift)}, status=201
    )


@login_required
@require_http_methods(["POST", "DELETE"])
def personal_shift_detail(request, pk):
    shift = get_object_or_404(PersonalShift, pk=pk, owner=request.user)
    if request.method == "DELETE":
        shift.delete()
        return JsonResponse({"ok": True, "deleted": str(pk)})

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = PersonalShiftForm(data, instance=shift)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    name = form.cleaned_data["name"]
    if (
        PersonalShift.objects.filter(owner=request.user, name__iexact=name)
        .exclude(pk=shift.pk)
        .exists()
    ):
        return JsonResponse(
            {
                "ok": False,
                "errors": {"name": ["A shift with this name already exists."]},
            },
            status=400,
        )
    shift = form.save(commit=False)
    shift.full_clean()
    shift.save()
    return JsonResponse(
        {"ok": True, "shift": _personal_shift_payload(shift)}
    )


NOTIFICATION_DESTINATIONS = {
    Notification.EventType.MEMBERSHIP_APPROVED: "core:settings",
    Notification.EventType.WELCOME: "core:settings",
    Notification.EventType.LOGIN: None,
    Notification.EventType.ACCOUNT: "core:index",
}


def _notification_payload(notification):
    created = timezone.localtime(notification.created_at)
    destination = NOTIFICATION_DESTINATIONS.get(notification.event_type)
    return {
        "id": str(notification.pk),
        "title": notification.title,
        "message": notification.message,
        "eventType": notification.event_type,
        "isRead": notification.is_read,
        "destinationUrl": reverse(destination) if destination else None,
        "createdAt": created.isoformat(),
        "createdLabel": created.strftime("%b %d, %Y · %H:%M"),
    }


@login_required
@require_http_methods(["GET"])
def notifications_api(request):
    queryset = Notification.objects.filter(owner=request.user)
    unread_count = queryset.filter(is_read=False).count()
    notifications = queryset.order_by("-created_at")[:50]
    return JsonResponse(
        {
            "ok": True,
            "unreadCount": unread_count,
            "notifications": [
                _notification_payload(notification)
                for notification in notifications
            ],
        }
    )


@login_required
@require_POST
def notification_read(request, pk):
    notification = get_object_or_404(
        Notification, pk=pk, owner=request.user
    )
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=["is_read", "read_at"])
    return JsonResponse(
        {
            "ok": True,
            "notification": _notification_payload(notification),
            "unreadCount": Notification.objects.filter(
                owner=request.user, is_read=False
            ).count(),
        }
    )


@login_required
@require_POST
def notifications_read_all(request):
    updated = Notification.objects.filter(
        owner=request.user, is_read=False
    ).update(is_read=True, read_at=timezone.now())
    return JsonResponse({"ok": True, "updated": updated, "unreadCount": 0})


def terms(request):
    return render(request, 'terms.html')


@login_required
def transaction(request):
    return render(request, 'transaction.html')


@login_required
def work_schedule(request):
    return render(request, 'work-schedule.html')


def _account_payload(account):
    return {
        "id": str(account.pk),
        "name": account.name,
        "type": account.account_type,
        "accountType": account.account_type,
        "accountTypeLabel": account.get_account_type_display(),
        "openingBalance": str(account.opening_balance),
        "openingBalanceDate": account.opening_balance_date.isoformat(),
        "description": account.description,
        "status": account.status,
        "isArchived": account.is_archived,
        "balance": str(account.calculate_balance()),
        "createdAt": account.created_at.isoformat(),
        "updatedAt": account.updated_at.isoformat(),
    }


def _account_request_data(request):
    if request.content_type and "application/json" in request.content_type:
        try:
            body = json.loads(request.body or b"{}")
        except (TypeError, ValueError):
            return None
        if not isinstance(body, dict):
            return None
        return body
    return request.POST.dict()


def _account_form_errors(form):
    return {
        field: [error["message"] for error in errors]
        for field, errors in form.errors.get_json_data().items()
    }


def _lock_owner(owner):
    """Serialize a user's financial writes so balances cannot be oversold."""
    return owner.__class__.objects.select_for_update().get(pk=owner.pk)


@login_required
@require_http_methods(["GET", "POST"])
def accounts(request):
    """List the authenticated user's accounts or create a new one."""
    if request.method == "GET":
        account_list = list(
            Account.objects.filter(owner=request.user).order_by(
                "created_at", "name"
            )
        )
        payload = [_account_payload(account) for account in account_list]
        total = sum(
            (Decimal(item["balance"]) for item in payload), Decimal("0.00")
        )
        return JsonResponse(
            {
                "ok": True,
                "accounts": payload,
                "count": len(payload),
                "totalBalance": str(total.quantize(Decimal("0.01"))),
            }
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = AccountForm(data, owner=request.user)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    with db_transaction.atomic():
        account = form.save()
    return JsonResponse(
        {"ok": True, "account": _account_payload(account)}, status=201
    )


@login_required
@require_http_methods(["GET", "POST"])
def account_detail(request, pk):
    """View or edit one account, enforcing ownership on the server."""
    account = get_object_or_404(Account, pk=pk, owner=request.user)

    if request.method == "GET":
        return JsonResponse({"ok": True, "account": _account_payload(account)})

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = AccountForm(data, instance=account, owner=request.user)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    with db_transaction.atomic():
        account = form.save()
    return JsonResponse({"ok": True, "account": _account_payload(account)})


@login_required
@require_POST
def account_status(request, pk):
    """Archive or restore an account without deleting its history."""
    account = get_object_or_404(Account, pk=pk, owner=request.user)
    data = _account_request_data(request) or {}
    status = (data.get("status") or "").strip().lower()
    if status not in Account.Status.values:
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "status": [
                        "Choose 'active' or 'archived' for the account status."
                    ]
                },
            },
            status=400,
        )
    with db_transaction.atomic():
        account.status = status
        account.save(update_fields=["status", "updated_at"])
    return JsonResponse({"ok": True, "account": _account_payload(account)})


@login_required
@require_POST
def account_delete(request, pk):
    """Delete only an account with no ledger history.

    Accounts referenced by any transaction must be archived instead so that
    financial history is never silently destroyed.
    """
    account = get_object_or_404(Account, pk=pk, owner=request.user)
    has_history = account.transactions.exists() or account.incoming_transfers.exists()
    if has_history:
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "__all__": [
                        "This account has transaction history. Archive it "
                        "instead of deleting it."
                    ]
                },
            },
            status=409,
        )
    with db_transaction.atomic():
        account.delete()
    return JsonResponse({"ok": True, "deleted": str(pk)})


def _transaction_payload(entry):
    return {
        "id": str(entry.pk),
        "type": entry.transaction_type,
        "amount": str(entry.amount),
        "date": entry.date.isoformat(),
        "accountId": str(entry.account_id),
        "toAccountId": (
            str(entry.destination_account_id)
            if entry.destination_account_id
            else None
        ),
        "category": entry.category,
        "description": entry.description,
        "paymentMethod": entry.payment_method,
        "notes": entry.notes,
        "allocationId": (
            str(entry.allocation_id) if entry.allocation_id else None
        ),
        "allocationName": (
            entry.allocation.name if entry.allocation_id else None
        ),
        "goalId": str(entry.goal_id) if entry.goal_id else None,
        "goalName": entry.goal.name if entry.goal_id else None,
        "clientReference": entry.client_reference,
        "createdAt": entry.created_at.isoformat(),
        "updatedAt": entry.updated_at.isoformat(),
    }


@login_required
@require_http_methods(["GET", "POST"])
def transactions(request):
    """List the authenticated user's transactions or create one."""
    if request.method == "GET":
        entries = (
            Transaction.objects.filter(owner=request.user)
            .select_related(
                "account", "destination_account", "allocation", "goal"
            )
            .order_by("-date", "-created_at")
        )
        payload = [_transaction_payload(entry) for entry in entries]
        return JsonResponse({"ok": True, "transactions": payload, "count": len(payload)})

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    client_reference = (data.get("clientReference") or "").strip() or None
    if client_reference:
        existing = Transaction.objects.filter(
            owner=request.user, client_reference=client_reference
        ).first()
        if existing:
            return JsonResponse(
                {
                    "ok": True,
                    "duplicate": True,
                    "transaction": _transaction_payload(existing),
                }
            )
    form = TransactionForm(data, owner=request.user)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    goal_id = (
        form.cleaned_data["goal"].pk
        if form.cleaned_data.get("goal") is not None
        else None
    )
    try:
        with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
            _lock_owner(request.user)
            if client_reference:
                existing = Transaction.objects.filter(
                    owner=request.user, client_reference=client_reference
                ).first()
                if existing:
                    return JsonResponse(
                        {
                            "ok": True,
                            "duplicate": True,
                            "transaction": _transaction_payload(existing),
                        }
                    )
            purchase_goal = None
            if goal_id:
                purchase_goal = get_object_or_404(
                    Goal.objects.select_for_update(),
                    pk=goal_id,
                    owner=request.user,
                )
                goal_error = goals_calc.purchase_link_error(
                    purchase_goal, request.user
                )
                if goal_error:
                    return JsonResponse(
                        {"ok": False, "errors": {"goal": [goal_error]}},
                        status=409,
                    )
            entry = form.save(commit=False)
            entry.owner = request.user
            entry.client_reference = client_reference
            entry.goal = purchase_goal
            entry.full_clean()
            entry.save()
            if purchase_goal is not None:
                goals_calc.release_goal_for_purchase(purchase_goal)
    except IntegrityError:
        if client_reference:
            existing = Transaction.objects.filter(
                owner=request.user, client_reference=client_reference
            ).first()
            if existing:
                return JsonResponse(
                    {
                        "ok": True,
                        "duplicate": True,
                        "transaction": _transaction_payload(existing),
                    }
                )
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "goal": [
                        "This goal already has a recorded purchase."
                    ]
                },
            },
            status=409,
        )
    return JsonResponse(
        {"ok": True, "transaction": _transaction_payload(entry)}, status=201
    )


@login_required
@require_http_methods(["GET", "POST"])
def transaction_detail(request, pk):
    """View or edit one transaction, enforcing ownership."""
    entry = get_object_or_404(
        Transaction.objects.select_related("goal"), pk=pk, owner=request.user
    )
    if request.method == "GET":
        return JsonResponse(
            {"ok": True, "transaction": _transaction_payload(entry)}
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
        _lock_owner(request.user)
        entry = get_object_or_404(
            Transaction.objects.select_for_update().select_related("goal"),
            pk=pk,
            owner=request.user,
        )
        old_goal_id = entry.goal_id
        old_goal = None
        if old_goal_id:
            old_goal = Goal.objects.select_for_update().get(
                pk=old_goal_id, owner=request.user
            )
        form = TransactionForm(data, instance=entry, owner=request.user)
        if not form.is_valid():
            return JsonResponse(
                {"ok": False, "errors": _account_form_errors(form)}, status=400
            )
        new_goal_id = (
            form.cleaned_data["goal"].pk
            if form.cleaned_data.get("goal") is not None
            else None
        )
        new_goal = None
        if new_goal_id:
            new_goal = Goal.objects.select_for_update().get(
                pk=new_goal_id, owner=request.user
            )
        if new_goal is not None and new_goal_id != old_goal_id:
            goal_error = goals_calc.purchase_link_error(
                new_goal, request.user
            )
            if goal_error:
                return JsonResponse(
                    {"ok": False, "errors": {"goal": [goal_error]}},
                    status=409,
                )
        if old_goal is not None and old_goal_id == new_goal_id:
            amount_error = goals_calc.purchase_amount_change_error(
                old_goal, form.cleaned_data["amount"]
            )
            if amount_error:
                return JsonResponse(
                    {
                        "ok": False,
                        "code": "goal_funding_exceeded",
                        "errors": {"amount": [amount_error]},
                    },
                    status=409,
                )
        if old_goal is not None and old_goal_id != new_goal_id:
            restore_error = goals_calc.restore_goal_funding_for_unlink(
                old_goal
            )
            if restore_error:
                return JsonResponse(
                    {
                        "ok": False,
                        "code": "goal_funding_reassigned",
                        "errors": {"__all__": [restore_error]},
                    },
                    status=409,
                )
        entry = form.save(commit=False)
        entry.owner = request.user
        entry.goal = new_goal
        entry.full_clean()
        entry.save()
        if new_goal is not None and new_goal_id != old_goal_id:
            goals_calc.release_goal_for_purchase(new_goal)
    return JsonResponse(
        {"ok": True, "transaction": _transaction_payload(entry)}
    )


@login_required
@require_POST
def transaction_delete(request, pk):
    entry = get_object_or_404(Transaction, pk=pk, owner=request.user)
    with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
        _lock_owner(request.user)
        entry = get_object_or_404(
            Transaction.objects.select_for_update().select_related("goal"),
            pk=pk,
            owner=request.user,
        )
        if entry.goal_id:
            goal = get_object_or_404(
                Goal.objects.select_for_update(),
                pk=entry.goal_id,
                owner=request.user,
            )
            restore_error = goals_calc.restore_goal_funding_for_unlink(goal)
            if restore_error:
                return JsonResponse(
                    {
                        "ok": False,
                        "code": "goal_funding_reassigned",
                        "errors": {"__all__": [restore_error]},
                    },
                    status=409,
                )
        entry.delete()
    return JsonResponse({"ok": True, "deleted": str(pk)})

def _month_start(value):
    """Parse YYYY-MM into the first day of that month."""
    if not value:
        return None
    try:
        year, month = (int(part) for part in str(value).split("-"))
        return date(year, month, 1)
    except (TypeError, ValueError):
        return None


def _budget_allocation_payload(allocation, actual=None):
    if actual is None:
        actual = financials.allocation_actual_spending(allocation)
    planned = financials.quantize_money(allocation.planned_amount)
    actual = financials.quantize_money(actual)
    return {
        "id": str(allocation.pk),
        "name": allocation.name,
        "purpose": allocation.category.name,
        "category": allocation.category.name,
        "categoryId": str(allocation.category_id),
        "financialMonth": allocation.budget.month_key,
        "plannedAmount": str(planned),
        "actualSpending": str(actual),
        "remainingAmount": str(
            financials.quantize_money(planned - actual)
        ),
        "overSpent": str(
            financials.quantize_money(max(Decimal("0.00"), actual - planned))
        ),
        "status": allocation.status,
        "description": allocation.description,
        "createdAt": allocation.created_at.isoformat(),
        "updatedAt": allocation.updated_at.isoformat(),
    }


def _budget_category_payload(category):
    return {
        "id": str(category.pk),
        "name": category.name,
        "createdAt": category.created_at.isoformat(),
    }


def _month_summary(owner, month, allocations):
    summary = financials.summarize_month(owner, month, allocations)
    allocation_details = [
        _budget_allocation_payload(row["allocation"], row["actual"])
        for row in summary["allocations"]
    ]
    return {
        "month": summary["month_key"],
        "monthlyIncome": str(summary["monthly_income"]),
        "totalAllocated": str(summary["total_allocated"]),
        "unallocatedFunds": str(summary["unallocated"]),
        "overAllocatedFunds": str(summary["over_allocated"]),
        "totalActualSpending": str(summary["total_actual_spending"]),
        "totalRemaining": str(summary["total_remaining"]),
        "allocationCount": summary["allocation_count"],
        "allocations": allocation_details,
    }


@login_required
@require_http_methods(["GET"])
def budget_api(request):
    """Return the authenticated user's budgets, categories and summaries."""
    requested_month = _month_start(request.GET.get("month"))
    if request.GET.get("month") and requested_month is None:
        return JsonResponse(
            {"ok": False, "errors": {"month": ["Use a valid YYYY-MM month."]}},
            status=400,
        )
    if requested_month is None:
        requested_month = date.today().replace(day=1)

    categories = list(
        BudgetCategory.objects.filter(owner=request.user).order_by("name")
    )
    allocations = list(
        BudgetAllocation.objects.filter(budget__owner=request.user)
        .select_related("budget", "category")
        .order_by("budget__month", "name")
    )

    grouped = {}
    for allocation in allocations:
        grouped.setdefault(allocation.budget.month, []).append(allocation)

    income_months = {
        entry.date.replace(day=1)
        for entry in Transaction.objects.filter(
            owner=request.user,
            transaction_type=Transaction.TransactionType.INCOME,
        ).only("date")
    }
    all_months = sorted(set(grouped) | income_months | {requested_month}, reverse=True)
    months = [
        _month_summary(request.user, month, grouped.get(month, []))
        for month in all_months
    ]
    selected = next(
        (
            summary
            for summary in months
            if summary["month"] == financials.month_key(requested_month)
        ),
        _month_summary(request.user, requested_month, []),
    )
    return JsonResponse(
        {
            "ok": True,
            "categories": [_budget_category_payload(item) for item in categories],
            "months": months,
            "selected": selected,
            "allocations": [
                _budget_allocation_payload(allocation) for allocation in allocations
            ],
        }
    )


@login_required
@require_http_methods(["GET", "POST"])
def budget_categories(request):
    if request.method == "GET":
        categories = BudgetCategory.objects.filter(owner=request.user).order_by(
            "name"
        )
        return JsonResponse(
            {
                "ok": True,
                "categories": [
                    _budget_category_payload(category) for category in categories
                ],
            }
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = BudgetCategoryForm(data, owner=request.user)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    with db_transaction.atomic():
        category = form.save()
    return JsonResponse(
        {"ok": True, "category": _budget_category_payload(category)}, status=201
    )


@login_required
@require_http_methods(["GET", "POST"])
def budget_category_detail(request, pk):
    category = get_object_or_404(
        BudgetCategory, pk=pk, owner=request.user
    )
    if request.method == "GET":
        return JsonResponse(
            {"ok": True, "category": _budget_category_payload(category)}
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = BudgetCategoryForm(data, owner=request.user, instance=category)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    with db_transaction.atomic():
        category = form.save()
    return JsonResponse(
        {"ok": True, "category": _budget_category_payload(category)}
    )


@login_required
@require_POST
def budget_category_delete(request, pk):
    category = get_object_or_404(
        BudgetCategory, pk=pk, owner=request.user
    )
    if category.allocations.exists():
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "__all__": [
                        "This category is used by existing allocations and "
                        "cannot be deleted."
                    ]
                },
            },
            status=409,
        )
    with db_transaction.atomic():
        category.delete()
    return JsonResponse({"ok": True, "deleted": str(pk)})


@login_required
@require_http_methods(["GET", "POST"])
def budget_allocations(request):
    if request.method == "GET":
        month = _month_start(request.GET.get("month"))
        queryset = BudgetAllocation.objects.filter(
            budget__owner=request.user
        ).select_related("budget", "category")
        if request.GET.get("month"):
            if month is None:
                return JsonResponse(
                    {
                        "ok": False,
                        "errors": {"month": ["Use a valid YYYY-MM month."]},
                    },
                    status=400,
                )
            queryset = queryset.filter(budget__month=month)
        allocations = queryset.order_by("budget__month", "name")
        return JsonResponse(
            {
                "ok": True,
                "allocations": [
                    _budget_allocation_payload(allocation)
                    for allocation in allocations
                ],
            }
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = BudgetAllocationForm(data, owner=request.user)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
        _lock_owner(request.user)
        candidate_total = savings_calc.projected_total_saved(
            request.user,
            new_amount=form.cleaned_data["planned_amount"],
            new_category_name=form.cleaned_data["category"].name,
            new_status=form.cleaned_data["status"],
        )
        savings_error = savings_calc.allocation_change_error(
            request.user, candidate_total
        )
        if savings_error:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [savings_error]}},
                status=400,
            )
        candidate_goals = goals_calc.projected_total_goals_funding(
            request.user,
            new_amount=form.cleaned_data["planned_amount"],
            new_category_name=form.cleaned_data["category"].name,
            new_status=form.cleaned_data["status"],
        )
        goals_error = goals_calc.allocation_change_error(
            request.user, candidate_goals
        )
        if goals_error:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [goals_error]}},
                status=400,
            )
        allocation = form.save()
    return JsonResponse(
        {"ok": True, "allocation": _budget_allocation_payload(allocation)},
        status=201,
    )


@login_required
@require_http_methods(["GET", "POST"])
def budget_allocation_detail(request, pk):
    allocation = get_object_or_404(
        BudgetAllocation.objects.select_related("budget", "category"),
        pk=pk,
        budget__owner=request.user,
    )
    if request.method == "GET":
        return JsonResponse(
            {"ok": True, "allocation": _budget_allocation_payload(allocation)}
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = BudgetAllocationForm(data, owner=request.user, instance=allocation)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
        _lock_owner(request.user)
        candidate_total = savings_calc.projected_total_saved(
            request.user,
            existing=allocation,
            new_amount=form.cleaned_data["planned_amount"],
            new_category_name=form.cleaned_data["category"].name,
            new_status=form.cleaned_data["status"],
        )
        savings_error = savings_calc.allocation_change_error(
            request.user, candidate_total
        )
        if savings_error:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [savings_error]}},
                status=400,
            )
        candidate_goals = goals_calc.projected_total_goals_funding(
            request.user,
            existing=allocation,
            new_amount=form.cleaned_data["planned_amount"],
            new_category_name=form.cleaned_data["category"].name,
            new_status=form.cleaned_data["status"],
        )
        goals_error = goals_calc.allocation_change_error(
            request.user, candidate_goals
        )
        if goals_error:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [goals_error]}},
                status=400,
            )
        allocation = form.save()
    return JsonResponse(
        {"ok": True, "allocation": _budget_allocation_payload(allocation)}
    )


@login_required
@require_POST
def budget_allocation_status(request, pk):
    allocation = get_object_or_404(
        BudgetAllocation,
        pk=pk,
        budget__owner=request.user,
    )
    data = _account_request_data(request) or {}
    status = (data.get("status") or "").strip().lower()
    if status not in BudgetAllocation.Status.values:
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "status": [
                        "Choose planned, active, completed or cancelled."
                    ]
                },
            },
            status=400,
        )
    with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
        _lock_owner(request.user)
        allocation = get_object_or_404(
            BudgetAllocation.objects.select_related("budget", "category"),
            pk=pk,
            budget__owner=request.user,
        )
        candidate_total = savings_calc.projected_total_saved(
            request.user,
            existing=allocation,
            new_status=status,
        )
        savings_error = savings_calc.allocation_change_error(
            request.user, candidate_total
        )
        if savings_error:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [savings_error]}},
                status=400,
            )
        candidate_goals = goals_calc.projected_total_goals_funding(
            request.user,
            existing=allocation,
            new_status=status,
        )
        goals_error = goals_calc.allocation_change_error(
            request.user, candidate_goals
        )
        if goals_error:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [goals_error]}},
                status=400,
            )
        allocation.status = status
        allocation.save(update_fields=["status", "updated_at"])
    return JsonResponse(
        {"ok": True, "allocation": _budget_allocation_payload(allocation)}
    )


@login_required
@require_POST
def budget_allocation_delete(request, pk):
    allocation = get_object_or_404(
        BudgetAllocation,
        pk=pk,
        budget__owner=request.user,
    )
    if allocation.linked_transactions.exists():
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "__all__": [
                        "This allocation has linked expenses. Cancel it to "
                        "keep the plan and its transaction history."
                    ]
                },
            },
            status=409,
        )
    with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
        _lock_owner(request.user)
        allocation = get_object_or_404(
            BudgetAllocation.objects.select_related("budget", "category"),
            pk=pk,
            budget__owner=request.user,
        )
        if allocation.linked_transactions.exists():
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "__all__": [
                            "This allocation has linked expenses. Cancel it to "
                            "keep the plan and its transaction history."
                        ]
                    },
                },
                status=409,
            )
        candidate_total = savings_calc.projected_total_saved(
            request.user,
            existing=allocation,
            remove=True,
        )
        savings_error = savings_calc.allocation_change_error(
            request.user, candidate_total
        )
        if savings_error:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [savings_error]}},
                status=409,
            )
        candidate_goals = goals_calc.projected_total_goals_funding(
            request.user,
            existing=allocation,
            remove=True,
        )
        goals_error = goals_calc.allocation_change_error(
            request.user, candidate_goals
        )
        if goals_error:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [goals_error]}},
                status=409,
            )
        allocation.delete()
    return JsonResponse({"ok": True, "deleted": str(pk)})


def _goal_assignment_payload(assignment):
    return {
        "id": str(assignment.pk),
        "amount": str(financials.quantize_money(assignment.amount)),
        "active": assignment.is_active,
        "reversed": assignment.reversed_at is not None,
        "released": assignment.is_released,
        "assignedAt": assignment.created_at.isoformat(),
        "reversedAt": (
            assignment.reversed_at.isoformat()
            if assignment.reversed_at is not None
            else None
        ),
        "releasedAt": (
            assignment.released_at.isoformat()
            if assignment.released_at is not None
            else None
        ),
    }


def _goal_payload(goal):
    assignments = list(goal.assignments.all())
    historical = [item for item in assignments if item.reversed_at is None]
    reserved = [item for item in historical if item.released_at is None]
    target = financials.quantize_money(goal.target_amount)
    assigned = financials.quantize_money(
        sum((item.amount for item in historical), ZERO_MONEY)
    )
    purchase = goals_calc.purchase_for_goal(goal)
    if purchase is None:
        reserved_amount = financials.quantize_money(
            sum((item.amount for item in reserved), ZERO_MONEY)
        )
        consumed_amount = ZERO_MONEY
        released_amount = ZERO_MONEY
    else:
        purchase_amount = financials.quantize_money(purchase.amount)
        reserved_amount = ZERO_MONEY
        consumed_amount = financials.quantize_money(
            min(assigned, purchase_amount)
        )
        released_amount = financials.quantize_money(
            max(ZERO_MONEY, assigned - purchase_amount)
        )
    remaining = financials.quantize_money(max(ZERO_MONEY, target - assigned))
    percentage = (
        financials.quantize_money((assigned / target) * Decimal("100"))
        if target > ZERO_MONEY
        else ZERO_MONEY
    )
    percentage = min(Decimal("100.00"), percentage)
    status = (
        "purchased"
        if purchase is not None
        else "fully_funded"
        if assigned >= target
        else "active"
    )
    return {
        "id": str(goal.pk),
        "name": goal.name,
        "targetAmount": str(target),
        "targetDate": goal.target_date.isoformat() if goal.target_date else None,
        "description": goal.description,
        "assignedAmount": str(assigned),
        "reservedAmount": str(reserved_amount),
        "consumedAmount": str(consumed_amount),
        "releasedAmount": str(released_amount),
        "remainingAmount": str(remaining),
        "progressPercent": str(percentage),
        "reached": assigned >= target,
        "status": status,
        "isPurchased": purchase is not None,
        "purchaseAmount": (
            str(financials.quantize_money(purchase.amount))
            if purchase is not None
            else None
        ),
        "purchaseDate": (
            purchase.date.isoformat() if purchase is not None else None
        ),
        "purchaseTransactionId": (
            str(purchase.pk) if purchase is not None else None
        ),
        "purchaseDescription": (
            purchase.description if purchase is not None else None
        ),
        "assignmentCount": 0 if purchase is not None else len(reserved),
        "createdAt": goal.created_at.isoformat(),
        "updatedAt": goal.updated_at.isoformat(),
        "assignments": [
            _goal_assignment_payload(item)
            for item in sorted(
                assignments,
                key=lambda item: item.created_at,
                reverse=True,
            )
        ],
    }


def _goals_payload(owner):
    goals = list(
        Goal.objects.filter(owner=owner)
        .select_related("purchase_transaction")
        .prefetch_related("assignments")
        .order_by("created_at", "name")
    )
    goal_rows = [_goal_payload(goal) for goal in goals]
    total_funding = goals_calc.total_goals_funding(owner)
    total_assigned = financials.quantize_money(
        sum(
            (Decimal(row["reservedAmount"]) for row in goal_rows),
            ZERO_MONEY,
        )
    )
    total_consumed = financials.quantize_money(
        sum(
            (Decimal(row["consumedAmount"]) for row in goal_rows),
            ZERO_MONEY,
        )
    )
    total_released = financials.quantize_money(
        sum(
            (Decimal(row["releasedAmount"]) for row in goal_rows),
            ZERO_MONEY,
        )
    )
    available = goals_calc.available_funding(owner)
    total_target = financials.quantize_money(
        sum((Decimal(row["targetAmount"]) for row in goal_rows), ZERO_MONEY)
    )
    remaining = financials.quantize_money(
        sum(
            (Decimal(row["remainingAmount"]) for row in goal_rows),
            ZERO_MONEY,
        )
    )
    reached = sum(1 for row in goal_rows if row["reached"])
    purchased = sum(1 for row in goal_rows if row["status"] == "purchased")
    fully_funded = sum(
        1 for row in goal_rows if row["status"] == "fully_funded"
    )
    active = sum(1 for row in goal_rows if row["status"] == "active")
    return {
        "summary": {
            "totalFunding": str(total_funding),
            "totalAssigned": str(total_assigned),
            "totalConsumed": str(total_consumed),
            "totalReleased": str(total_released),
            "availableFunding": str(available),
            "totalTarget": str(total_target),
            "remainingToTarget": str(remaining),
            "goalCount": len(goal_rows),
            "activeGoalCount": active,
            "historyGoalCount": fully_funded + purchased,
            "fullyFundedCount": fully_funded,
            "purchasedCount": purchased,
            "reachedCount": reached,
        },
        "goals": goal_rows,
    }


def _goal_client_reference(data):
    value = data.get("clientReference") or data.get("client_reference") or ""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _goal_form_data(data):
    form_data = dict(data)
    if "targetAmount" in data and "target_amount" not in form_data:
        form_data["target_amount"] = data.get("targetAmount")
    if "targetDate" in data and "target_date" not in form_data:
        form_data["target_date"] = data.get("targetDate")
    return form_data


@login_required
@require_http_methods(["GET"])
def goals_api(request):
    """Return cumulative Goals funding and the user's goals."""
    return JsonResponse({"ok": True, **_goals_payload(request.user)})


@login_required
@require_POST
def goal_create(request):
    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    reference = _goal_client_reference(data)
    if len(reference) > 64:
        return JsonResponse(
            {
                "ok": False,
                "errors": {"__all__": ["Invalid request reference."]},
            },
            status=400,
        )
    form = GoalForm(_goal_form_data(data), owner=request.user)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
        _lock_owner(request.user)
        if reference:
            existing = Goal.objects.filter(
                owner=request.user, client_reference=reference
            ).first()
            if existing is not None:
                return JsonResponse(
                    {"ok": True, **_goals_payload(request.user)}
                )
        goal = form.save(commit=False)
        goal.owner = request.user
        goal.client_reference = reference or None
        goal.full_clean()
        goal.save()
    return JsonResponse(
        {"ok": True, **_goals_payload(request.user)}, status=201
    )


@login_required
@require_http_methods(["GET", "POST"])
def goal_detail(request, pk):
    goal = get_object_or_404(Goal, pk=pk, owner=request.user)
    if request.method == "GET":
        goal = (
            Goal.objects.filter(pk=goal.pk, owner=request.user)
            .prefetch_related("assignments")
            .get()
        )
        return JsonResponse(
            {
                "ok": True,
                "goal": _goal_payload(goal),
                **_goals_payload(request.user),
            }
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    form = GoalForm(_goal_form_data(data), instance=goal, owner=request.user)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
        _lock_owner(request.user)
        goal = get_object_or_404(Goal, pk=pk, owner=request.user)
        purchase = goals_calc.purchase_for_goal(goal)
        submitted_target = form.cleaned_data.get("target_amount")
        if (
            purchase is not None
            and submitted_target is not None
            and financials.quantize_money(submitted_target)
            != financials.quantize_money(goal.target_amount)
        ):
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "target_amount": [
                            "This goal has already been purchased. Its "
                            "original target is kept as history and cannot "
                            "be changed."
                        ]
                    },
                },
                status=409,
            )
        goal = form.save(commit=False)
        goal.pk = pk
        goal.owner = request.user
        goal.full_clean()
        goal.save()
    return JsonResponse({"ok": True, **_goals_payload(request.user)})


@login_required
@require_POST
def goal_delete(request, pk):
    with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
        _lock_owner(request.user)
        goal = get_object_or_404(Goal, pk=pk, owner=request.user)
        if goal.assignments.exists():
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "__all__": [
                            "This goal has funding history and cannot be "
                            "deleted. Goals with any assignment history are "
                            "kept so your records remain accurate."
                        ]
                    },
                },
                status=409,
            )
        goal.delete()
    return JsonResponse({"ok": True, **_goals_payload(request.user)})


@login_required
@require_POST
def goal_assignments(request, pk):
    goal = get_object_or_404(Goal, pk=pk, owner=request.user)
    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    reference = _goal_client_reference(data)
    if len(reference) > 64:
        return JsonResponse(
            {
                "ok": False,
                "errors": {"__all__": ["Invalid request reference."]},
            },
            status=400,
        )
    form = GoalAssignmentForm(data)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": _account_form_errors(form)}, status=400
        )
    with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
        _lock_owner(request.user)
        goal = get_object_or_404(Goal, pk=pk, owner=request.user)
        if goals_calc.purchase_for_goal(goal) is not None:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "__all__": [
                            "This goal has already been purchased and cannot "
                            "receive new funding."
                        ]
                    },
                },
                status=409,
            )
        if reference:
            existing = GoalAssignment.objects.filter(
                owner=request.user, client_reference=reference
            ).first()
            if existing is not None:
                return JsonResponse(
                    {"ok": True, **_goals_payload(request.user)}
                )
        amount = form.cleaned_data["amount"]
        projected_assigned = financials.quantize_money(
            goals_calc.total_assigned(request.user) + amount
        )
        shortfall = goals_calc.funding_shortfall(
            request.user, assigned=projected_assigned
        )
        if shortfall > ZERO_MONEY:
            return JsonResponse(
                {
                    "ok": False,
                    "code": "goal_funding_exceeded",
                    "errors": {
                        "amount": [goals_calc.FUNDING_EXCEEDED_MESSAGE]
                    },
                },
                status=400,
            )
        assignment = GoalAssignment(
            owner=request.user,
            goal=goal,
            amount=amount,
            client_reference=reference or None,
        )
        assignment.full_clean()
        assignment.save()
    return JsonResponse(
        {"ok": True, **_goals_payload(request.user)}, status=201
    )


@login_required
@require_POST
def goal_assignment_reverse(request, pk, assignment_pk):
    with goals_calc.goal_write_lock(request.user.pk), db_transaction.atomic():
        _lock_owner(request.user)
        assignment = get_object_or_404(
            GoalAssignment.objects.select_related("goal"),
            pk=assignment_pk,
            goal_id=pk,
            owner=request.user,
            goal__owner=request.user,
        )
        if assignment.reversed_at is not None:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "__all__": [
                            "This funding assignment was already reversed."
                        ]
                    },
                },
                status=409,
            )
        if assignment.released_at is not None:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "__all__": [
                            "This funding was released when the goal purchase "
                            "was recorded and cannot be reversed separately."
                        ]
                    },
                },
                status=409,
            )
        assignment.reversed_at = timezone.now()
        assignment.save(update_fields=["reversed_at"])
    return JsonResponse({"ok": True, **_goals_payload(request.user)})


def _savings_withdrawal_payload(withdrawal):
    return {
        "id": str(withdrawal.pk),
        "amount": str(financials.quantize_money(withdrawal.amount)),
        "date": withdrawal.date.isoformat(),
        "reason": withdrawal.reason,
        "createdAt": withdrawal.created_at.isoformat(),
        "updatedAt": withdrawal.updated_at.isoformat(),
    }


def _savings_history(owner, selected_month):
    monthly = savings_calc.monthly_savings(owner)
    withdrawals = list(
        SavingsWithdrawal.objects.filter(owner=owner).order_by(
            "-date", "-created_at"
        )
    )
    by_month = {}
    for withdrawal in withdrawals:
        by_month.setdefault(withdrawal.date.replace(day=1), []).append(withdrawal)

    rows = []
    for month in savings_calc.month_starts(owner, selected_month):
        month_withdrawals = by_month.get(month, [])
        saved = monthly.get(month, ZERO_MONEY)
        withdrawn = financials.quantize_money(
            sum(
                (withdrawal.amount for withdrawal in month_withdrawals),
                ZERO_MONEY,
            )
        )
        rows.append(
            {
                "month": financials.month_key(month),
                "saved": str(saved),
                "withdrawn": str(withdrawn),
                "net": str(financials.quantize_money(saved - withdrawn)),
                "withdrawals": [
                    _savings_withdrawal_payload(withdrawal)
                    for withdrawal in month_withdrawals
                ],
            }
        )
    return rows, withdrawals


def _savings_summary(owner, selected_month):
    monthly = savings_calc.monthly_savings(owner)
    monthly_saved = monthly.get(selected_month, ZERO_MONEY)
    monthly_withdrawn = savings_calc.monthly_withdrawn(owner, selected_month)
    total_saved = savings_calc.total_saved(owner)
    total_withdrawn = savings_calc.total_withdrawn(owner)
    return {
        "totalSaved": str(total_saved),
        "totalWithdrawn": str(total_withdrawn),
        "currentBalance": str(
            financials.quantize_money(total_saved - total_withdrawn)
        ),
        "monthlySaved": str(monthly_saved),
        "monthlyWithdrawn": str(monthly_withdrawn),
        "monthlyNet": str(
            financials.quantize_money(monthly_saved - monthly_withdrawn)
        ),
    }


@login_required
@require_http_methods(["GET"])
def savings_api(request):
    """Return Budget-derived savings totals and persisted withdrawals."""
    requested_month = _month_start(request.GET.get("month"))
    if request.GET.get("month") and requested_month is None:
        return JsonResponse(
            {"ok": False, "errors": {"month": ["Use a valid YYYY-MM month."]}},
            status=400,
        )
    if requested_month is None:
        requested_month = date.today().replace(day=1)

    history, withdrawals = _savings_history(request.user, requested_month)
    return JsonResponse(
        {
            "ok": True,
            "month": financials.month_key(requested_month),
            "summary": _savings_summary(request.user, requested_month),
            "months": history,
            "withdrawals": [
                _savings_withdrawal_payload(withdrawal)
                for withdrawal in withdrawals
            ],
        }
    )


@login_required
@require_http_methods(["GET", "POST"])
def savings_withdrawals(request):
    if request.method == "GET":
        withdrawals = SavingsWithdrawal.objects.filter(
            owner=request.user
        ).order_by("-date", "-created_at")
        return JsonResponse(
            {
                "ok": True,
                "withdrawals": [
                    _savings_withdrawal_payload(withdrawal)
                    for withdrawal in withdrawals
                ],
            }
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    client_reference = (data.get("clientReference") or "").strip() or None
    if client_reference:
        existing = SavingsWithdrawal.objects.filter(
            owner=request.user, client_reference=client_reference
        ).first()
        if existing:
            return JsonResponse(
                {
                    "ok": True,
                    "duplicate": True,
                    "withdrawal": _savings_withdrawal_payload(existing),
                }
            )

    try:
        with db_transaction.atomic():
            _lock_owner(request.user)
            if client_reference:
                existing = SavingsWithdrawal.objects.filter(
                    owner=request.user, client_reference=client_reference
                ).first()
                if existing:
                    return JsonResponse(
                        {
                            "ok": True,
                            "duplicate": True,
                            "withdrawal": _savings_withdrawal_payload(existing),
                        }
                    )
            form = SavingsWithdrawalForm(data, owner=request.user)
            if not form.is_valid():
                return JsonResponse(
                    {"ok": False, "errors": _account_form_errors(form)},
                    status=400,
                )
            available = savings_calc.current_balance(request.user)
            if form.cleaned_data["amount"] > available:
                return JsonResponse(
                    {
                        "ok": False,
                        "errors": {
                            "amount": [
                                "Withdrawal cannot exceed the available "
                                f"savings balance of {available:,.2f}."
                            ]
                        },
                    },
                    status=400,
                )
            withdrawal = form.save(commit=False)
            withdrawal.client_reference = client_reference
            withdrawal.full_clean()
            withdrawal.save()
    except IntegrityError:
        if client_reference:
            existing = SavingsWithdrawal.objects.filter(
                owner=request.user, client_reference=client_reference
            ).first()
            if existing:
                return JsonResponse(
                    {
                        "ok": True,
                        "duplicate": True,
                        "withdrawal": _savings_withdrawal_payload(existing),
                    }
                )
        raise
    return JsonResponse(
        {
            "ok": True,
            "withdrawal": _savings_withdrawal_payload(withdrawal),
            "summary": _savings_summary(
                request.user, withdrawal.date.replace(day=1)
            ),
        },
        status=201,
    )


@login_required
@require_http_methods(["GET", "POST"])
def savings_withdrawal_detail(request, pk):
    withdrawal = get_object_or_404(
        SavingsWithdrawal, pk=pk, owner=request.user
    )
    if request.method == "GET":
        return JsonResponse(
            {"ok": True, "withdrawal": _savings_withdrawal_payload(withdrawal)}
        )

    data = _account_request_data(request)
    if data is None:
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Invalid request body."]}},
            status=400,
        )
    with db_transaction.atomic():
        _lock_owner(request.user)
        withdrawal = get_object_or_404(
            SavingsWithdrawal, pk=pk, owner=request.user
        )
        form = SavingsWithdrawalForm(
            data, instance=withdrawal, owner=request.user
        )
        if not form.is_valid():
            return JsonResponse(
                {"ok": False, "errors": _account_form_errors(form)},
                status=400,
            )
        available = savings_calc.current_balance(
            request.user, exclude_pk=withdrawal.pk
        )
        if form.cleaned_data["amount"] > available:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "amount": [
                            "Withdrawal cannot exceed the available savings "
                            f"balance of {available:,.2f}."
                        ]
                    },
                },
                status=400,
            )
        withdrawal = form.save()
    return JsonResponse(
        {"ok": True, "withdrawal": _savings_withdrawal_payload(withdrawal)}
    )


@login_required
@require_POST
def savings_withdrawal_delete(request, pk):
    with db_transaction.atomic():
        _lock_owner(request.user)
        withdrawal = get_object_or_404(
            SavingsWithdrawal, pk=pk, owner=request.user
        )
        withdrawal.delete()
    return JsonResponse({"ok": True, "deleted": str(pk)})


@login_required
@require_POST
def transaction_allocation(request, pk):
    entry = get_object_or_404(Transaction, pk=pk, owner=request.user)
    data = _account_request_data(request) or {}
    allocation_id = (data.get("allocationId") or "").strip()
    if allocation_id:
        allocation = get_object_or_404(
            BudgetAllocation.objects.select_related("budget"),
            pk=allocation_id,
            budget__owner=request.user,
        )
        if entry.transaction_type != Transaction.TransactionType.EXPENSE:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "allocation": [
                            "Only expense transactions can be linked to a "
                            "budget allocation."
                        ]
                    },
                },
                status=400,
            )
        if allocation.budget.month != entry.date.replace(day=1):
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "allocation": [
                            "The expense date must fall within the "
                            "allocation's financial month."
                        ]
                    },
                },
                status=400,
            )
    else:
        allocation = None
    with db_transaction.atomic():
        entry.allocation = allocation
        entry.save(update_fields=["allocation", "updated_at"])
    return JsonResponse(
        {"ok": True, "transaction": _transaction_payload(entry)}
    )