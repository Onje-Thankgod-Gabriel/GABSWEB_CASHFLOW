"""Authenticated JSON API for the Daily Planner and Project Planner.

All planner records are owned by exactly one authenticated user. Every query
and mutation is scoped to ``request.user`` so changing an id in a request can
never expose or modify another user's data.

Estimated project costs are planning-only. Nothing in this module creates or
modifies financial accounts, transactions, budgets, savings or goals.
"""

import json
from datetime import date, time
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction as db_transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods, require_POST

from .models import (
    DailyMeeting,
    DailyPlan,
    DailyPlanTopThree,
    DailyTask,
    Project,
    ProjectMilestone,
)

PLAN_STATUS_DEFAULT = "unspecified"
TASK_PRIORITIES = {choice for choice, _ in DailyTask.Priority.choices}
PROJECT_STATUSES = {choice for choice, _ in Project.Status.choices}
MILESTONE_STATUSES = {
    choice for choice, _ in ProjectMilestone.Status.choices
}
MAX_MIGRATION_RECORDS = 5000


class PlannerError(Exception):
    """A safe, user-facing planner validation error."""

    def __init__(self, message, field="__all__"):
        super().__init__(message)
        self.field = field


def _request_data(request):
    if request.content_type and "application/json" in request.content_type:
        try:
            body = json.loads(request.body or b"{}")
        except (TypeError, ValueError):
            return None
        return body if isinstance(body, dict) else None
    return request.POST.dict()


def _errors(payload, status=400):
    return JsonResponse({"ok": False, "errors": payload}, status=status)


def _error(message, field="__all__", status=400):
    return _errors({field: [message]}, status=status)


def _parse_date(value, label="date"):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise PlannerError(f"Enter a valid {label}.")


def _parse_time(value, label="time"):
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value
    try:
        return time.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise PlannerError(f"Enter a valid {label}.")


def _clean_text(value, max_length=None, required=False, label="text"):
    text = str(value or "").strip()
    if required and not text:
        raise PlannerError(f"Enter a {label}.")
    if max_length is not None:
        text = text[:max_length]
    return text


def _parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _parse_decimal(value):
    try:
        amount = Decimal(str(value if value not in (None, "") else "0"))
    except (InvalidOperation, TypeError, ValueError):
        raise PlannerError("Estimated cost must be a valid amount.")
    if amount < 0:
        raise PlannerError("Estimated cost cannot be negative.")
    if not amount.is_finite():
        raise PlannerError("Estimated cost must be a valid amount.")
    return amount.quantize(Decimal("0.01"))


def _parse_position(value, default=0):
    try:
        position = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, position)


def _iso(value):
    return value.isoformat() if value else None


def _task_payload(task):
    return {
        "id": str(task.pk),
        "title": task.title,
        "description": task.description,
        "completed": task.completed,
        "priority": task.priority,
        "isTopThree": task.top_three_entries.filter(
            plan_id=task.plan_id
        ).exists(),
        "completedAt": _iso(task.completed_at),
        "position": task.position,
        "createdAt": _iso(task.created_at),
        "updatedAt": _iso(task.updated_at),
    }


def _meeting_payload(meeting):
    return {
        "id": str(meeting.pk),
        "title": meeting.title,
        "date": meeting.date.isoformat(),
        "startTime": meeting.start_time.strftime("%H:%M")
        if meeting.start_time
        else "",
        "endTime": meeting.end_time.strftime("%H:%M")
        if meeting.end_time
        else "",
        "location": meeting.location,
        "notes": meeting.notes,
        "createdAt": _iso(meeting.created_at),
        "updatedAt": _iso(meeting.updated_at),
    }


def _plan_payload(plan):
    tasks = list(plan.tasks.all())
    meetings = list(plan.meetings.all())
    top_three = list(
        plan.top_three_entries.select_related("task").order_by("position")
    )
    top_ids = [str(entry.task_id) for entry in top_three]
    top_set = set(top_ids)
    for task in tasks:
        task._planner_is_top_three = str(task.pk) in top_set
    return {
        "id": str(plan.pk),
        "date": plan.date.isoformat(),
        "dayStatus": plan.day_status,
        "notes": plan.notes,
        "remarks": plan.remarks,
        "tasks": [
            {
                "id": str(task.pk),
                "title": task.title,
                "description": task.description,
                "completed": task.completed,
                "priority": task.priority,
                "isTopThree": task._planner_is_top_three,
                "completedAt": _iso(task.completed_at),
                "position": task.position,
                "createdAt": _iso(task.created_at),
                "updatedAt": _iso(task.updated_at),
            }
            for task in tasks
        ],
        "meetings": [_meeting_payload(meeting) for meeting in meetings],
        "topThreeTaskIds": top_ids,
        "score": plan.score(),
        "createdAt": _iso(plan.created_at),
        "updatedAt": _iso(plan.updated_at),
    }


def _milestone_payload(milestone):
    return {
        "id": str(milestone.pk),
        "title": milestone.title,
        "description": milestone.description,
        "dueDate": milestone.due_date.isoformat(),
        "status": milestone.status,
        "completionDate": _iso(milestone.completion_date),
        "notes": milestone.notes,
        "createdAt": _iso(milestone.created_at),
        "updatedAt": _iso(milestone.updated_at),
    }


def _project_payload(project):
    return {
        "id": str(project.pk),
        "name": project.name,
        "objectives": project.objectives,
        "executionPlan": project.execution_plan,
        "startDate": project.start_date.isoformat(),
        "endDate": project.end_date.isoformat(),
        "deadline": project.deadline.isoformat(),
        "estimatedCost": float(project.estimated_cost),
        "status": project.status,
        "notes": project.notes,
        "milestones": [
            _milestone_payload(milestone)
            for milestone in project.milestones.all()
        ],
        "progress": project.progress(),
        "createdAt": _iso(project.created_at),
        "updatedAt": _iso(project.updated_at),
    }


def _snapshot_payload(user):
    plans = (
        DailyPlan.objects.filter(owner=user)
        .prefetch_related(
            "tasks",
            "meetings",
            "top_three_entries__task",
        )
        .order_by("date")
    )
    projects = (
        Project.objects.filter(owner=user)
        .prefetch_related("milestones")
        .order_by("created_at", "name")
    )
    return {
        "ok": True,
        "dailyPlans": [_plan_payload(plan) for plan in plans],
        "projects": [_project_payload(project) for project in projects],
    }


def _owned_plan(user, plan_id):
    return get_object_or_404(DailyPlan, pk=plan_id, owner=user)


def _owned_task(plan, task_id):
    return get_object_or_404(DailyTask, pk=task_id, plan=plan)


def _owned_meeting(plan, meeting_id):
    return get_object_or_404(DailyMeeting, pk=meeting_id, plan=plan)


def _owned_project(user, project_id):
    return get_object_or_404(Project, pk=project_id, owner=user)


def _owned_milestone(project, milestone_id):
    return get_object_or_404(
        ProjectMilestone, pk=milestone_id, project=project
    )


def _plan_values(payload):
    values = {}
    if "date" in payload:
        values["date"] = _parse_date(payload.get("date"), "plan date")
    if "dayStatus" in payload or "day_status" in payload:
        value = payload.get("dayStatus", payload.get("day_status"))
        values["day_status"] = _clean_text(
            value, max_length=20, label="day status"
        ) or PLAN_STATUS_DEFAULT
    if "notes" in payload:
        values["notes"] = str(payload.get("notes") or "")
    if "remarks" in payload:
        values["remarks"] = str(payload.get("remarks") or "")
    return values


def _task_values(payload, instance=None):
    values = {}
    if "title" in payload or instance is None:
        values["title"] = _clean_text(
            payload.get("title"), max_length=180, required=True, label="task title"
        )
    if "description" in payload:
        values["description"] = str(payload.get("description") or "")
    if "priority" in payload:
        priority = str(payload.get("priority") or "medium").strip().lower()
        if priority not in TASK_PRIORITIES:
            raise PlannerError("Choose a valid task priority.")
        values["priority"] = priority
    if "completed" in payload:
        values["completed"] = _parse_bool(payload.get("completed"))
        if values["completed"]:
            raw_completed = payload.get("completedAt")
            parsed = parse_datetime(raw_completed) if raw_completed else None
            values["completed_at"] = parsed or timezone.now()
        else:
            values["completed_at"] = None
    if "position" in payload:
        values["position"] = _parse_position(payload.get("position"))
    return values


def _meeting_values(payload, plan_date, instance=None):
    values = {"date": plan_date}
    if "title" in payload or instance is None:
        values["title"] = _clean_text(
            payload.get("title"),
            max_length=180,
            required=True,
            label="appointment title",
        )
    if "startTime" in payload or "start_time" in payload:
        values["start_time"] = _parse_time(
            payload.get("startTime", payload.get("start_time")), "start time"
        )
    if "endTime" in payload or "end_time" in payload:
        values["end_time"] = _parse_time(
            payload.get("endTime", payload.get("end_time")), "end time"
        )
    if "location" in payload:
        values["location"] = _clean_text(
            payload.get("location"), max_length=240
        )
    if "notes" in payload:
        values["notes"] = str(payload.get("notes") or "")
    return values


def _project_values(payload, instance=None):
    values = {}
    if "name" in payload or instance is None:
        values["name"] = _clean_text(
            payload.get("name"),
            max_length=180,
            required=True,
            label="project name",
        )
    if "objectives" in payload:
        values["objectives"] = str(payload.get("objectives") or "")
    if "executionPlan" in payload or "execution_plan" in payload:
        values["execution_plan"] = str(
            payload.get("executionPlan", payload.get("execution_plan")) or ""
        )
    if "startDate" in payload or "start_date" in payload:
        values["start_date"] = _parse_date(
            payload.get("startDate", payload.get("start_date")), "start date"
        )
    if "endDate" in payload or "end_date" in payload:
        values["end_date"] = _parse_date(
            payload.get("endDate", payload.get("end_date")), "end date"
        )
    if "deadline" in payload:
        values["deadline"] = _parse_date(payload.get("deadline"), "deadline")
    if "estimatedCost" in payload or "estimated_cost" in payload:
        values["estimated_cost"] = _parse_decimal(
            payload.get("estimatedCost", payload.get("estimated_cost"))
        )
    if "status" in payload:
        status = str(payload.get("status") or "").strip().lower()
        if status not in PROJECT_STATUSES:
            raise PlannerError("Choose a valid project status.")
        values["status"] = status
    if "notes" in payload:
        values["notes"] = str(payload.get("notes") or "")
    return values


def _validate_project_dates(values, instance=None):
    start_date = values.get("start_date", instance.start_date if instance else None)
    end_date = values.get("end_date", instance.end_date if instance else None)
    deadline = values.get("deadline", instance.deadline if instance else None)
    if not start_date or not end_date or not deadline:
        raise PlannerError("Choose a start date, end date and deadline.")
    if end_date < start_date:
        raise PlannerError(
            "The project end date must be on or after the start date.",
            field="endDate",
        )
    if deadline < start_date:
        raise PlannerError(
            "The project deadline must be on or after the start date.",
            field="deadline",
        )
    return start_date, deadline


def _milestone_values(payload, project, instance=None):
    values = {}
    if "title" in payload or instance is None:
        values["title"] = _clean_text(
            payload.get("title"),
            max_length=180,
            required=True,
            label="milestone title",
        )
    if "description" in payload:
        values["description"] = str(payload.get("description") or "")
    if "dueDate" in payload or "due_date" in payload:
        values["due_date"] = _parse_date(
            payload.get("dueDate", payload.get("due_date")), "due date"
        )
    if "status" in payload:
        status = str(payload.get("status") or "").strip().lower()
        if status not in MILESTONE_STATUSES:
            raise PlannerError("Choose a valid milestone status.")
        values["status"] = status
    if "completionDate" in payload or "completion_date" in payload:
        raw = payload.get(
            "completionDate", payload.get("completion_date")
        )
        values["completion_date"] = _parse_date(raw, "completion date") if raw else None
    if "notes" in payload:
        values["notes"] = str(payload.get("notes") or "")

    due_date = values.get(
        "due_date", instance.due_date if instance else None
    )
    if not due_date:
        raise PlannerError("Choose a milestone due date.")
    start_date = project.start_date
    deadline = project.deadline
    if due_date < start_date:
        raise PlannerError(
            "The milestone due date must be on or after the project start date.",
            field="dueDate",
        )
    if due_date > deadline:
        raise PlannerError(
            "The milestone due date must be on or before the project deadline.",
            field="dueDate",
        )

    status = values.get("status", instance.status if instance else "pending")
    completion_date = values.get(
        "completion_date",
        instance.completion_date if instance else None,
    )
    if status == ProjectMilestone.Status.COMPLETED:
        if not completion_date:
            completion_date = timezone.localdate()
        values["completion_date"] = completion_date
    else:
        values["completion_date"] = None
    return values


def _apply(instance, values):
    for field, value in values.items():
        setattr(instance, field, value)


def _set_top_three(plan, task_ids):
    tasks = list(plan.tasks.all())
    by_id = {str(task.pk): task for task in tasks}
    cleaned = []
    seen = set()
    for raw in task_ids:
        task_id = str(raw or "").strip()
        if not task_id:
            raise PlannerError("Top Three task references cannot be empty.")
        if task_id in seen:
            raise PlannerError("Top Three task references must be unique.")
        if task_id not in by_id:
            raise PlannerError("A Top Three task does not belong to this plan.")
        seen.add(task_id)
        cleaned.append(task_id)
    if len(cleaned) > 3:
        raise PlannerError("Top Three can contain at most three tasks.")
    plan.top_three_entries.all().delete()
    DailyPlanTopThree.objects.bulk_create(
        [
            DailyPlanTopThree(plan=plan, task=by_id[task_id], position=index)
            for index, task_id in enumerate(cleaned)
        ]
    )


def _replace_plan_state(plan):
    plan.refresh_from_db()
    return _plan_payload(plan)


def _replace_project_state(project):
    project.refresh_from_db()
    return _project_payload(project)


@login_required
@require_http_methods(["GET"])
def planner_api(request):
    response = JsonResponse(_snapshot_payload(request.user))
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Vary"] = "Cookie"
    return response


@login_required
@require_http_methods(["GET"])
def daily_plan_by_date(request, day):
    try:
        plan_date = _parse_date(day, "plan date")
    except PlannerError as error:
        return _error(str(error))
    plan = DailyPlan.objects.filter(
        owner=request.user, date=plan_date
    ).first()
    if plan is None:
        return JsonResponse({"ok": False}, status=404)
    return JsonResponse({"ok": True, "dailyPlan": _plan_payload(plan)})


@login_required
@require_POST
def daily_plan_create(request):
    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    try:
        values = _plan_values(payload)
        plan_date = values.get("date")
        if not plan_date:
            raise PlannerError("Choose a plan date.")
        if DailyPlan.objects.filter(
            owner=request.user, date=plan_date
        ).exists():
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "date": [
                            "A daily plan already exists for this date."
                        ]
                    },
                },
                status=409,
            )
        plan = DailyPlan(owner=request.user, **values)
        plan.full_clean()
    except (PlannerError, ValidationError) as error:
        return _validation_error_response(error)

    try:
        with db_transaction.atomic():
            plan.save()
    except IntegrityError:
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "date": ["A daily plan already exists for this date."]
                },
            },
            status=409,
        )
    return JsonResponse(
        {"ok": True, "dailyPlan": _plan_payload(plan)}, status=201
    )


@login_required
@require_POST
def daily_plan_ensure(request):
    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    try:
        plan_date = _parse_date(payload.get("date"), "plan date")
    except PlannerError as error:
        return _error(str(error))

    with db_transaction.atomic():
        plan, created = DailyPlan.objects.get_or_create(
            owner=request.user,
            date=plan_date,
            defaults={"day_status": PLAN_STATUS_DEFAULT},
        )
    return JsonResponse(
        {"ok": True, "created": created, "dailyPlan": _plan_payload(plan)},
        status=201 if created else 200,
    )


@login_required
@require_http_methods(["GET", "PATCH", "PUT", "DELETE"])
def daily_plan_detail(request, pk):
    plan = _owned_plan(request.user, pk)
    if request.method == "GET":
        return JsonResponse({"ok": True, "dailyPlan": _plan_payload(plan)})
    if request.method == "DELETE":
        with db_transaction.atomic():
            plan.delete()
        return JsonResponse({"ok": True, "deleted": str(pk)})

    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    try:
        values = _plan_values(payload)
        new_date = values.get("date")
        if new_date and new_date != plan.date:
            if DailyPlan.objects.filter(
                owner=request.user, date=new_date
            ).exclude(pk=plan.pk).exists():
                return JsonResponse(
                    {
                        "ok": False,
                        "errors": {
                            "date": [
                                "A daily plan already exists for this date."
                            ]
                        },
                    },
                    status=409,
                )
        _apply(plan, values)
        plan.full_clean()
    except (PlannerError, ValidationError) as error:
        return _validation_error_response(error)

    try:
        with db_transaction.atomic():
            plan.save()
    except IntegrityError:
        return JsonResponse(
            {
                "ok": False,
                "errors": {"date": ["A daily plan already exists for this date."]},
            },
            status=409,
        )
    return JsonResponse({"ok": True, "dailyPlan": _plan_payload(plan)})


@login_required
@require_POST
def daily_task_create(request, pk):
    plan = _owned_plan(request.user, pk)
    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    try:
        values = _task_values(payload)
    except PlannerError as error:
        return _error(str(error))

    try:
        with db_transaction.atomic():
            plan = DailyPlan.objects.select_for_update().get(pk=plan.pk)
            if "position" not in values:
                current_max = plan.tasks.aggregate(max_position=Max("position"))[
                    "max_position"
                ]
                values["position"] = 0 if current_max is None else current_max + 1
            task = DailyTask(plan=plan, **values)
            task.full_clean()
            task.save()
            if _parse_bool(payload.get("isTopThree")):
                current = list(plan.top_three_entries.order_by("position"))
                if len(current) >= 3:
                    raise PlannerError("Top Three already has three tasks.")
                DailyPlanTopThree.objects.create(
                    plan=plan, task=task, position=len(current)
                )
    except (PlannerError, ValidationError) as error:
        return _validation_error_response(error)
    return JsonResponse(
        {"ok": True, "task": _task_payload(task), "dailyPlan": _plan_payload(plan)},
        status=201,
    )


@login_required
@require_http_methods(["PATCH", "PUT", "DELETE"])
def daily_task_detail(request, pk, task_pk):
    plan = _owned_plan(request.user, pk)
    task = _owned_task(plan, task_pk)
    if request.method == "DELETE":
        with db_transaction.atomic():
            task.delete()
        return JsonResponse(
            {"ok": True, "deleted": str(task_pk), "dailyPlan": _plan_payload(plan)}
        )

    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    try:
        values = _task_values(payload, instance=task)
    except PlannerError as error:
        return _error(str(error))

    try:
        with db_transaction.atomic():
            _apply(task, values)
            task.full_clean()
            task.save()
            if "isTopThree" in payload:
                desired = _parse_bool(payload.get("isTopThree"))
                existing = DailyPlanTopThree.objects.filter(
                    plan=plan, task=task
                ).exists()
                if desired and not existing:
                    if plan.top_three_entries.count() >= 3:
                        raise PlannerError("Top Three already has three tasks.")
                    next_position = (
                        plan.top_three_entries.aggregate(Max("position"))[
                            "position__max"
                        ]
                    )
                    DailyPlanTopThree.objects.create(
                        plan=plan,
                        task=task,
                        position=0 if next_position is None else next_position + 1,
                    )
                elif not desired and existing:
                    DailyPlanTopThree.objects.filter(
                        plan=plan, task=task
                    ).delete()
    except (PlannerError, ValidationError) as error:
        return _validation_error_response(error)
    return JsonResponse(
        {"ok": True, "task": _task_payload(task), "dailyPlan": _plan_payload(plan)}
    )


@login_required
@require_http_methods(["PUT", "POST"])
def daily_top_three(request, pk):
    plan = _owned_plan(request.user, pk)
    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    raw_ids = payload.get(
        "taskIds", payload.get("topThreeTaskIds", payload.get("tasks"))
    )
    if not isinstance(raw_ids, list):
        return _error("Top Three task ids must be a list.", field="taskIds")
    try:
        with db_transaction.atomic():
            plan = DailyPlan.objects.select_for_update().get(pk=plan.pk)
            _set_top_three(plan, raw_ids)
    except PlannerError as error:
        return _error(str(error), field="taskIds")
    return JsonResponse({"ok": True, "dailyPlan": _plan_payload(plan)})


@login_required
@require_POST
def daily_meeting_create(request, pk):
    plan = _owned_plan(request.user, pk)
    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    try:
        values = _meeting_values(payload, plan.date)
        meeting = DailyMeeting(plan=plan, **values)
        meeting.full_clean()
    except (PlannerError, ValidationError) as error:
        return _validation_error_response(error)

    with db_transaction.atomic():
        meeting.save()
    return JsonResponse(
        {
            "ok": True,
            "meeting": _meeting_payload(meeting),
            "dailyPlan": _plan_payload(plan),
        },
        status=201,
    )


@login_required
@require_http_methods(["PATCH", "PUT", "DELETE"])
def daily_meeting_detail(request, pk, meeting_pk):
    plan = _owned_plan(request.user, pk)
    meeting = _owned_meeting(plan, meeting_pk)
    if request.method == "DELETE":
        with db_transaction.atomic():
            meeting.delete()
        return JsonResponse(
            {
                "ok": True,
                "deleted": str(meeting_pk),
                "dailyPlan": _plan_payload(plan),
            }
        )

    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    try:
        values = _meeting_values(payload, plan.date, instance=meeting)
        _apply(meeting, values)
        meeting.full_clean()
    except (PlannerError, ValidationError) as error:
        return _validation_error_response(error)

    with db_transaction.atomic():
        meeting.save()
    return JsonResponse(
        {
            "ok": True,
            "meeting": _meeting_payload(meeting),
            "dailyPlan": _plan_payload(plan),
        }
    )


@login_required
@require_POST
def project_create(request):
    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    try:
        values = _project_values(payload)
        _validate_project_dates(values)
        milestones = payload.get("milestones")
        if milestones is not None and not isinstance(milestones, list):
            raise PlannerError("Project milestones must be a list.")
        if isinstance(milestones, list) and len(milestones) > 1000:
            raise PlannerError("A project cannot have more than 1000 milestones.")
    except PlannerError as error:
        return _error(str(error))

    try:
        with db_transaction.atomic():
            project = Project(owner=request.user, **values)
            project.full_clean()
            project.save()
            for item in milestones or []:
                if not isinstance(item, dict):
                    raise PlannerError("Each milestone must be an object.")
                milestone_values = _milestone_values(item, project)
                milestone = ProjectMilestone(project=project, **milestone_values)
                milestone.full_clean()
                milestone.save()
    except (PlannerError, ValidationError) as error:
        return _validation_error_response(error)
    return JsonResponse(
        {"ok": True, "project": _project_payload(project)}, status=201
    )


@login_required
@require_http_methods(["GET", "PATCH", "PUT", "DELETE"])
def project_detail(request, pk):
    project = _owned_project(request.user, pk)
    if request.method == "GET":
        return JsonResponse({"ok": True, "project": _project_payload(project)})
    if request.method == "DELETE":
        with db_transaction.atomic():
            project.delete()
        return JsonResponse({"ok": True, "deleted": str(pk)})

    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    try:
        values = _project_values(payload, instance=project)
        start_date, deadline = _validate_project_dates(values, project)
        for milestone in project.milestones.all():
            if milestone.due_date < start_date:
                raise PlannerError(
                    "This change would put an existing milestone before the "
                    "project start date. Update that milestone first.",
                    field="startDate",
                )
            if milestone.due_date > deadline:
                raise PlannerError(
                    "This change would put an existing milestone after the "
                    "project deadline. Update that milestone first.",
                    field="deadline",
                )
        _apply(project, values)
        project.full_clean()
    except PlannerError as error:
        return _error(str(error), field=getattr(error, "field", "__all__"))
    except ValidationError as error:
        return _validation_error_response(error)

    with db_transaction.atomic():
        project.save()
    return JsonResponse({"ok": True, "project": _project_payload(project)})


@login_required
@require_POST
def milestone_create(request, pk):
    project = _owned_project(request.user, pk)
    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    try:
        values = _milestone_values(payload, project)
        milestone = ProjectMilestone(project=project, **values)
        milestone.full_clean()
    except (PlannerError, ValidationError) as error:
        return _validation_error_response(error)

    with db_transaction.atomic():
        milestone.save()
    return JsonResponse(
        {
            "ok": True,
            "milestone": _milestone_payload(milestone),
            "project": _project_payload(project),
        },
        status=201,
    )


@login_required
@require_http_methods(["PATCH", "PUT", "DELETE"])
def milestone_detail(request, pk, milestone_pk):
    project = _owned_project(request.user, pk)
    milestone = _owned_milestone(project, milestone_pk)
    if request.method == "DELETE":
        with db_transaction.atomic():
            milestone.delete()
        return JsonResponse(
            {
                "ok": True,
                "deleted": str(milestone_pk),
                "project": _project_payload(project),
            }
        )

    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    try:
        values = _milestone_values(payload, project, instance=milestone)
        _apply(milestone, values)
        milestone.full_clean()
    except (PlannerError, ValidationError) as error:
        return _validation_error_response(error)

    with db_transaction.atomic():
        milestone.save()
    return JsonResponse(
        {
            "ok": True,
            "milestone": _milestone_payload(milestone),
            "project": _project_payload(project),
        }
    )


def _validation_error_response(error):
    if isinstance(error, PlannerError):
        field = getattr(error, "field", "__all__")
        return _error(str(error), field=field)
    return _errors(error.message_dict if hasattr(error, "message_dict") else {
        "__all__": [str(error)]
    })


def _migration_task_rows(plan_payload):
    tasks = plan_payload.get("tasks") or []
    if not isinstance(tasks, list):
        raise PlannerError("Daily plan tasks must be a list.")
    rows = []
    for item in tasks:
        if not isinstance(item, dict):
            raise PlannerError("Each task must be an object.")
        values = _task_values(item)
        completed = values.get("completed", False)
        rows.append(
            {
                "values": values,
                "local_id": str(item.get("id") or ""),
                "is_top_three": _parse_bool(item.get("isTopThree")),
                "completed": completed,
            }
        )
    return rows


def _migration_meeting_rows(plan_payload, plan_date):
    meetings = plan_payload.get("meetings") or []
    if not isinstance(meetings, list):
        raise PlannerError("Daily plan appointments must be a list.")
    rows = []
    for item in meetings:
        if not isinstance(item, dict):
            raise PlannerError("Each appointment must be an object.")
        rows.append(_meeting_values(item, plan_date))
    return rows


def _migrate_daily_plans(user, incoming):
    imported = 0
    skipped = 0
    conflicts = []
    invalid = []
    for index, item in enumerate(incoming):
        if not isinstance(item, dict):
            invalid.append(
                {"index": index, "date": "", "message": "Daily plan must be an object."}
            )
            continue
        try:
            plan_date = _parse_date(item.get("date"), "plan date")
            if DailyPlan.objects.filter(owner=user, date=plan_date).exists():
                skipped += 1
                conflicts.append(
                    {
                        "date": plan_date.isoformat(),
                        "message": (
                            "A server daily plan already exists for this date. "
                            "The server plan was kept and the local plan was "
                            "backed up in this browser."
                        ),
                    }
                )
                continue
            plan_values = _plan_values(item)
            plan_values["date"] = plan_date
            task_rows = _migration_task_rows(item)
            meeting_rows = _migration_meeting_rows(item, plan_date)
            top_ids = item.get("topThreeTaskIds") or []
            if not isinstance(top_ids, list):
                raise PlannerError("Top Three task references must be a list.")
        except PlannerError as error:
            invalid.append(
                {
                    "index": index,
                    "date": item.get("date"),
                    "message": str(error),
                }
            )
            continue

        try:
            with db_transaction.atomic():
                plan = DailyPlan(owner=user, **plan_values)
                plan.full_clean()
                plan.save()
                by_local_id = {}
                created_tasks = []
                for row in task_rows:
                    task = DailyTask(plan=plan, **row["values"])
                    task.full_clean()
                    task.save()
                    created_tasks.append((row, task))
                    if row["local_id"]:
                        by_local_id[row["local_id"]] = task
                mapped_ids = []
                if top_ids:
                    missing = [
                        str(task_id)
                        for task_id in top_ids
                        if str(task_id) not in by_local_id
                    ]
                    if missing:
                        raise PlannerError(
                            "Top Three references a task that is not in the local plan."
                        )
                    mapped_ids = [
                        str(by_local_id[str(task_id)].pk)
                        for task_id in top_ids
                    ]
                else:
                    mapped_ids = [
                        str(task.pk)
                        for row, task in created_tasks
                        if row["is_top_three"]
                    ]
                _set_top_three(plan, mapped_ids)
                for meeting_values in meeting_rows:
                    meeting = DailyMeeting(plan=plan, **meeting_values)
                    meeting.full_clean()
                    meeting.save()
            imported += 1
        except (PlannerError, ValidationError, IntegrityError) as error:
            invalid.append(
                {
                    "index": index,
                    "date": plan_date.isoformat(),
                    "message": str(error),
                }
            )
    return imported, skipped, conflicts, invalid


def _migrate_projects(user, incoming):
    imported = 0
    skipped = 0
    conflicts = []
    invalid = []
    for index, item in enumerate(incoming):
        if not isinstance(item, dict):
            invalid.append(
                {"index": index, "name": "", "message": "Project must be an object."}
            )
            continue
        local_id = str(item.get("id") or "").strip()[:120]
        try:
            if local_id and Project.objects.filter(
                owner=user, client_reference=local_id
            ).exists():
                skipped += 1
                continue
            values = _project_values(item)
            _validate_project_dates(values)
            raw_milestones = item.get("milestones") or []
            if not isinstance(raw_milestones, list):
                raise PlannerError("Project milestones must be a list.")
        except PlannerError as error:
            invalid.append(
                {
                    "index": index,
                    "name": item.get("name"),
                    "message": str(error),
                }
            )
            continue

        try:
            with db_transaction.atomic():
                project = Project(
                    owner=user,
                    client_reference=local_id or None,
                    **values,
                )
                project.full_clean()
                project.save()
                for raw_milestone in raw_milestones:
                    if not isinstance(raw_milestone, dict):
                        raise PlannerError("Each milestone must be an object.")
                    milestone_values = _milestone_values(raw_milestone, project)
                    milestone = ProjectMilestone(
                        project=project, **milestone_values
                    )
                    milestone.full_clean()
                    milestone.save()
            imported += 1
        except (PlannerError, ValidationError, IntegrityError) as error:
            invalid.append(
                {
                    "index": index,
                    "name": item.get("name"),
                    "message": str(error),
                }
            )
    return imported, skipped, conflicts, invalid


@login_required
@require_POST
def planner_migrate(request):
    payload = _request_data(request)
    if payload is None:
        return _error("Invalid request body.")
    daily_plans = payload.get("dailyPlans", [])
    projects = payload.get("projects", [])
    if not isinstance(daily_plans, list) or not isinstance(projects, list):
        return _error("Provide daily plan and project lists.")
    if (
        len(daily_plans) > MAX_MIGRATION_RECORDS
        or len(projects) > MAX_MIGRATION_RECORDS
    ):
        return _error(
            f"Import at most {MAX_MIGRATION_RECORDS} records of each type at a time."
        )

    (
        plans_imported,
        plans_skipped,
        plan_conflicts,
        plan_invalid,
    ) = _migrate_daily_plans(request.user, daily_plans)
    (
        projects_imported,
        projects_skipped,
        project_conflicts,
        project_invalid,
    ) = _migrate_projects(request.user, projects)

    return JsonResponse(
        {
            "ok": True,
            "dailyPlansImported": plans_imported,
            "dailyPlansSkipped": plans_skipped,
            "dailyPlanConflicts": plan_conflicts,
            "dailyPlanInvalid": plan_invalid,
            "projectsImported": projects_imported,
            "projectsSkipped": projects_skipped,
            "projectConflicts": project_conflicts,
            "projectInvalid": project_invalid,
        }
    )
