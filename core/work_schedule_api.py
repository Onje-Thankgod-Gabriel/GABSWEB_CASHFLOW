"""Authenticated JSON API for the Work Schedule backend."""

import hashlib
import json
from datetime import date, time, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction as db_transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from django.contrib.auth.decorators import login_required

from . import work_schedule as engine
from .models import User, WorkRotationConfig, WorkScheduleRecord

APPROVED = User.AccountStatus.APPROVED


def _request_data(request):
    if request.content_type and "application/json" in request.content_type:
        try:
            body = json.loads(request.body or b"{}")
        except (TypeError, ValueError):
            return None
        return body if isinstance(body, dict) else None
    return request.POST.dict()


def _errors(payload):
    return JsonResponse({"ok": False, "errors": payload}, status=400)


def _approved(user):
    return user.account_status == APPROVED


def _require_approved(user):
    if not _approved(user):
        return JsonResponse(
            {
                "ok": False,
                "message": "Workplace rotation access requires approval.",
                "errors": {
                    "__all__": [
                        "Workplace rotation and generation require approved "
                        "GCTN or Apex Pay access."
                    ]
                },
            },
            status=403,
        )
    return None


def _parse_date(value, label="date"):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise engine.RotationError(f"Enter a valid {label}.")


def _parse_time(value, label="time"):
    try:
        return time.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise engine.RotationError(f"Enter a valid {label}.")


def _config_payload(config_row, config):
    if config is None:
        config = engine.normalize_config(config_row)
    anchor = None
    if config_row is not None and config_row.configured:
        engine_anchor, _ = engine.anchor_from_config(config_row)
        if engine_anchor is not None:
            anchor = engine.anchor_payload(engine_anchor, config)
    return {
        "configured": bool(config_row and config_row.configured),
        "workingDaysPerCycle": config["working_days_per_cycle"],
        "offDaysPerCycle": config["off_days_per_cycle"],
        "shiftSequence": config["shift_sequence"],
        "shiftTimes": config["shift_times"],
        "anchor": (
            {
                "date": anchor["date"],
                "shiftType": anchor["shiftType"],
                "cyclePosition": anchor["position"],
                "startTime": anchor["startTime"],
                "endTime": anchor["endTime"],
            }
            if anchor
            else {
                "date": "",
                "shiftType": "",
                "cyclePosition": 0,
                "startTime": "",
                "endTime": "",
            }
        ),
    }


def _rotation_payload(resolution, saved_row):
    config = resolution.get("config") or engine.normalize_config(saved_row)
    effective = _config_payload(saved_row, config)
    anchor = resolution.get("anchor")
    if anchor:
        effective["anchor"] = {
            "date": anchor["date"],
            "shiftType": anchor["shiftType"],
            "cyclePosition": anchor["position"],
            "startTime": anchor["startTime"],
            "endTime": anchor["endTime"],
        }
        if resolution.get("source") == "records":
            effective["configured"] = True
    return effective


def _readiness_payload(resolution, approved):
    return {
        "ready": bool(resolution.get("ready")),
        "canGenerate": bool(approved and resolution.get("ready")),
        "source": resolution.get("source"),
        "issues": resolution.get("issues") or [],
        "conflicts": resolution.get("conflicts") or [],
        "resolution": resolution.get("resolution") or {},
    }


def _token(user, start, end, config_row, records):
    digest = hashlib.sha256()
    digest.update(str(user.pk).encode("utf-8"))
    digest.update(start.isoformat().encode("ascii"))
    digest.update(end.isoformat().encode("ascii"))
    if config_row is None:
        digest.update(b"|no-config|")
    else:
        digest.update(
            "|".join(
                [
                    str(config_row.configured),
                    str(config_row.working_days_per_cycle),
                    str(config_row.off_days_per_cycle),
                    json.dumps(config_row.shift_sequence, sort_keys=True),
                    json.dumps(config_row.shift_times, sort_keys=True),
                    config_row.anchor_date.isoformat()
                    if config_row.anchor_date
                    else "",
                    config_row.anchor_shift or "",
                    str(config_row.anchor_position),
                    config_row.updated_at.isoformat()
                    if config_row.updated_at
                    else "",
                ]
            ).encode("utf-8")
        )
    for record in sorted(records, key=lambda item: (item.date, str(item.pk))):
        digest.update(
            "|".join(
                [
                    str(record.pk),
                    record.date.isoformat(),
                    record.shift_type,
                    record.status,
                    engine._time_text(record.start_time),
                    engine._time_text(record.end_time),
                    record.source,
                    str(record.is_exception),
                    record.updated_at.isoformat() if record.updated_at else "",
                ]
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _range_values(data):
    month = (data.get("month") or "").strip()
    if month:
        try:
            first = date.fromisoformat(month + "-01")
        except (TypeError, ValueError):
            raise engine.RotationError("Choose a valid month.")
        if first.month == 12:
            end = date(first.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(first.year, first.month + 1, 1) - timedelta(days=1)
        return first, end
    raw_start = data.get("start") or data.get("startDate") or data.get("from")
    raw_end = data.get("end") or data.get("endDate") or data.get("to")
    if not raw_start or not raw_end:
        raise engine.RotationError("Choose a start and end date.")
    start = _parse_date(raw_start, "start date")
    end = _parse_date(raw_end, "end date")
    if end < start:
        raise engine.RotationError("The end date must be on or after the start date.")
    return start, end


def _record_values(payload, instance=None, force_manual=False):
    if not isinstance(payload, dict):
        raise engine.RotationError("Schedule entry must be an object.")
    values = {}
    values["date"] = _parse_date(payload.get("date"), "work date")

    shift_type = str(
        payload.get("shiftType", payload.get("shift_type", "")) or ""
    ).strip().lower()
    if shift_type not in engine.VALID_SHIFTS:
        raise engine.RotationError("Choose morning, afternoon, night or day off.")

    status = str(payload.get("status", "") or "").strip().lower()
    if shift_type == engine.SHIFT_OFF:
        status = "off"
    elif status not in ("working", "unassigned"):
        status = "working"
    if status == "working" and shift_type == engine.SHIFT_OFF:
        raise engine.RotationError("Choose a working shift or mark the day off.")

    start_value = payload.get("startTime", payload.get("start_time", ""))
    end_value = payload.get("endTime", payload.get("end_time", ""))
    if status == "off" or (
        status == "unassigned" and not start_value and not end_value
    ):
        start = None
        end = None
    else:
        start = _parse_time(start_value, "start time")
        end = _parse_time(end_value, "end time")
        if start == end:
            raise engine.RotationError("Start and end times cannot be the same.")

    values.update(
        {
            "shift_type": shift_type,
            "status": status,
            "start_time": start,
            "end_time": end,
            "team": " ".join(str(payload.get("team") or "").split())[:80],
            "notes": " ".join(str(payload.get("notes") or "").split())[:300],
            "is_exception": bool(
                payload.get("isException", payload.get("is_exception", False))
            ),
        }
    )
    if force_manual:
        values["source"] = WorkScheduleRecord.Source.MANUAL
    elif payload.get("source") == "generated":
        values["source"] = WorkScheduleRecord.Source.GENERATED
    else:
        values["source"] = WorkScheduleRecord.Source.MANUAL
    return values


def _apply_values(record, values):
    for field, value in values.items():
        setattr(record, field, value)


def _parse_config(payload):
    data = payload if isinstance(payload, dict) else {}
    rotation = data.get("rotation") if isinstance(data.get("rotation"), dict) else data
    anchor = data.get("anchor") if isinstance(data.get("anchor"), dict) else data
    raw_times = rotation.get("shiftTimes", rotation.get("shift_times", {}))
    config = engine.normalize_config(
        {
            "working_days_per_cycle": rotation.get(
                "workingDaysPerCycle",
                rotation.get("working_days_per_cycle", 3),
            ),
            "off_days_per_cycle": rotation.get(
                "offDaysPerCycle", rotation.get("off_days_per_cycle", 2)
            ),
            "shift_sequence": rotation.get(
                "shiftSequence", rotation.get("shift_sequence", engine.DEFAULT_SEQUENCE)
            ),
            "shift_times": raw_times,
        }
    )
    anchor_date = anchor.get("date") or anchor.get("anchor_date") or ""
    anchor_shift = (
        anchor.get("shiftType") or anchor.get("anchor_shift") or ""
    ).strip().lower()
    anchor_position = anchor.get(
        "cyclePosition", anchor.get("anchor_position", 0)
    )
    if not anchor_date:
        raise engine.RotationError("Choose a rotation reference date.")
    if anchor_shift not in config["shift_sequence"]:
        raise engine.RotationError(
            "Choose the shift block that applies to the reference cycle."
        )
    try:
        position = int(anchor_position)
    except (TypeError, ValueError):
        raise engine.RotationError("Choose the reference date's cycle position.")
    if position < 0 or position >= config["block_length"]:
        raise engine.RotationError(
            "Choose a valid position in the work/off cycle."
        )
    anchor_date = _parse_date(anchor_date, "rotation reference date")
    return config, anchor_date, anchor_shift, position


def _temp_config(user, config, anchor_date, anchor_shift, position):
    return WorkRotationConfig(
        owner=user,
        configured=True,
        working_days_per_cycle=config["working_days_per_cycle"],
        off_days_per_cycle=config["off_days_per_cycle"],
        shift_sequence=config["shift_sequence"],
        shift_times=config["shift_times"],
        anchor_date=anchor_date,
        anchor_shift=anchor_shift,
        anchor_position=position,
    )


def _schedule_records(user, include_generated=True):
    queryset = WorkScheduleRecord.objects.filter(owner=user)
    if not include_generated:
        queryset = queryset.filter(source=WorkScheduleRecord.Source.MANUAL)
    return list(queryset.order_by("date", "created_at"))


@login_required
@require_http_methods(["GET"])
def schedule_api(request):
    approved = _approved(request.user)
    include_generated = approved
    records = _schedule_records(request.user, include_generated=include_generated)
    config_row = (
        WorkRotationConfig.objects.filter(owner=request.user).first()
        if approved
        else None
    )
    if approved:
        resolution = engine.resolve_rotation(records, config_row)
    else:
        resolution = {
            "ready": False,
            "source": None,
            "config": engine.normalize_config(None),
            "anchor": None,
            "issues": [
                "Workplace rotation is available only to approved GCTN or "
                "Apex Pay members."
            ],
            "conflicts": [],
        }
    response = JsonResponse(
        {
            "ok": True,
            "approved": approved,
            "records": [engine.record_payload(record) for record in records],
            "rotation": _rotation_payload(resolution, config_row),
            "anchorSource": resolution.get("source"),
            "readiness": _readiness_payload(resolution, approved),
        }
    )
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Vary"] = "Cookie"
    return response


@login_required
@require_POST
def schedule_record_create(request):
    payload = _request_data(request)
    if payload is None:
        return _errors({"__all__": ["Invalid request body."]})
    try:
        values = _record_values(payload, force_manual=True)
        if (
            values["is_exception"]
            and not _approved(request.user)
        ):
            values["is_exception"] = False
        if WorkScheduleRecord.objects.filter(
            owner=request.user, date=values["date"]
        ).exists():
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "date": [
                            "A schedule entry already exists for this date. "
                            "Edit that entry instead."
                        ]
                    },
                },
                status=409,
            )
        record = WorkScheduleRecord(owner=request.user, **values)
        record.full_clean()
    except engine.RotationError as error:
        return _errors({"__all__": [str(error)]})
    except ValidationError as error:
        return _errors(error.message_dict)

    try:
        with db_transaction.atomic():
            record.save()
    except IntegrityError:
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "date": [
                        "A schedule entry already exists for this date. Edit "
                        "that entry instead."
                    ]
                },
            },
            status=409,
        )
    return JsonResponse(
        {"ok": True, "record": engine.record_payload(record)}, status=201
    )


@login_required
@require_http_methods(["POST", "PATCH", "PUT", "DELETE"])
def schedule_record_detail(request, pk):
    record = get_object_or_404(
        WorkScheduleRecord, pk=pk, owner=request.user
    )
    if record.source == WorkScheduleRecord.Source.GENERATED and not _approved(
        request.user
    ):
        return JsonResponse({"ok": False}, status=404)

    if request.method == "DELETE":
        with db_transaction.atomic():
            record.delete()
        return JsonResponse({"ok": True, "deleted": str(pk)})

    payload = _request_data(request)
    if payload is None:
        return _errors({"__all__": ["Invalid request body."]})
    merged = engine.record_payload(record)
    merged.update(payload)
    merged["source"] = WorkScheduleRecord.Source.MANUAL
    try:
        values = _record_values(merged, force_manual=True)
        if values["is_exception"] and not _approved(request.user):
            values["is_exception"] = False
        if values["date"] != record.date and WorkScheduleRecord.objects.filter(
            owner=request.user, date=values["date"]
        ).exists():
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "date": [
                            "A schedule entry already exists for this date. "
                            "Edit that entry instead."
                        ]
                    },
                },
                status=409,
            )
        _apply_values(record, values)
        record.full_clean()
    except engine.RotationError as error:
        return _errors({"__all__": [str(error)]})
    except ValidationError as error:
        return _errors(error.message_dict)

    try:
        with db_transaction.atomic():
            record.save()
    except IntegrityError:
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "date": [
                        "A schedule entry already exists for this date. Edit "
                        "that entry instead."
                    ]
                },
            },
            status=409,
        )
    return JsonResponse({"ok": True, "record": engine.record_payload(record)})


@login_required
@require_http_methods(["PUT", "POST"])
def schedule_config_api(request):
    denied = _require_approved(request.user)
    if denied:
        return denied
    payload = _request_data(request)
    if payload is None:
        return _errors({"__all__": ["Invalid request body."]})
    try:
        config, anchor_date, anchor_shift, position = _parse_config(payload)
    except engine.RotationError as error:
        return _errors({"__all__": [str(error)]})

    records = _schedule_records(request.user, include_generated=True)
    temp = _temp_config(
        request.user, config, anchor_date, anchor_shift, position
    )
    resolution = engine.resolve_rotation(records, temp)
    if not resolution["ready"]:
        return JsonResponse(
            {
                "ok": False,
                "errors": {"__all__": resolution["issues"]},
                "conflicts": resolution.get("conflicts") or [],
                "readiness": _readiness_payload(resolution, True),
            },
            status=409,
        )

    with db_transaction.atomic():
        config_row, _ = WorkRotationConfig.objects.select_for_update().get_or_create(
            owner=request.user
        )
        config_row.configured = True
        config_row.working_days_per_cycle = config["working_days_per_cycle"]
        config_row.off_days_per_cycle = config["off_days_per_cycle"]
        config_row.shift_sequence = config["shift_sequence"]
        config_row.shift_times = config["shift_times"]
        config_row.anchor_date = anchor_date
        config_row.anchor_shift = anchor_shift
        config_row.anchor_position = position
        config_row.save()

    resolution = engine.resolve_rotation(
        _schedule_records(request.user, include_generated=True), config_row
    )
    return JsonResponse(
        {
            "ok": True,
            "rotation": _rotation_payload(resolution, config_row),
            "anchorSource": resolution.get("source"),
            "readiness": _readiness_payload(resolution, True),
        }
    )


@login_required
@require_POST
def schedule_preview_api(request):
    denied = _require_approved(request.user)
    if denied:
        return denied
    payload = _request_data(request)
    if payload is None:
        return _errors({"__all__": ["Invalid request body."]})
    try:
        start, end = _range_values(payload)
    except engine.RotationError as error:
        return _errors({"__all__": [str(error)]})

    records = _schedule_records(request.user, include_generated=True)
    config_row = WorkRotationConfig.objects.filter(owner=request.user).first()
    try:
        resolution, rows = engine.preview_rows(
            records, config_row, start, end
        )
    except engine.RotationNotReady as error:
        return JsonResponse(
            {
                "ok": False,
                "message": str(error),
                "issues": error.issues,
                "conflicts": error.conflicts,
                "resolution": error.resolution,
                "readiness": _readiness_payload(
                    {
                        "ready": False,
                        "source": None,
                        "issues": error.issues,
                        "conflicts": error.conflicts,
                        "resolution": error.resolution,
                    },
                    True,
                ),
            },
            status=409,
        )
    except engine.RotationError as error:
        return _errors({"__all__": [str(error)]})

    token = _token(request.user, start, end, config_row, records)
    return JsonResponse(
        {
            "ok": True,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "token": token,
            "rotation": _rotation_payload(resolution, config_row),
            "anchorSource": resolution.get("source"),
            "rows": rows,
            "summary": engine.preview_summary(rows),
            "readiness": _readiness_payload(resolution, True),
        }
    )


@login_required
@require_POST
def schedule_confirm_api(request):
    denied = _require_approved(request.user)
    if denied:
        return denied
    payload = _request_data(request)
    if payload is None:
        return _errors({"__all__": ["Invalid request body."]})
    token = str(payload.get("token") or "").strip()
    if not token:
        return _errors({"token": ["A preview token is required."]})
    try:
        start, end = _range_values(payload)
    except engine.RotationError as error:
        return _errors({"__all__": [str(error)]})

    with db_transaction.atomic():
        User.objects.select_for_update().get(pk=request.user.pk)
        records = list(
            WorkScheduleRecord.objects.select_for_update()
            .filter(owner=request.user)
            .order_by("date")
        )
        config_row = (
            WorkRotationConfig.objects.select_for_update()
            .filter(owner=request.user)
            .first()
        )
        expected_token = _token(request.user, start, end, config_row, records)
        if expected_token != token:
            return JsonResponse(
                {
                    "ok": False,
                    "message": (
                        "This preview is out of date because the schedule changed. "
                        "Generate a new preview and review it again."
                    ),
                    "code": "stale_preview",
                },
                status=409,
            )
        try:
            resolution, rows = engine.preview_rows(
                records, config_row, start, end
            )
        except engine.RotationNotReady as error:
            return JsonResponse(
                {
                    "ok": False,
                    "message": str(error),
                    "issues": error.issues,
                    "conflicts": error.conflicts,
                },
                status=409,
            )

        existing = {record.date: record for record in records}
        created = 0
        updated = 0
        preserved = 0
        conflicts = 0
        unchanged = 0
        for row in rows:
            action = row["action"]
            target_date = date.fromisoformat(row["date"])
            if action == "create":
                expected = row["expected"]
                WorkScheduleRecord.objects.create(
                    owner=request.user,
                    date=target_date,
                    shift_type=expected["shiftType"],
                    status=expected["status"],
                    start_time=time.fromisoformat(expected["startTime"])
                    if expected["startTime"]
                    else None,
                    end_time=time.fromisoformat(expected["endTime"])
                    if expected["endTime"]
                    else None,
                    source=WorkScheduleRecord.Source.GENERATED,
                )
                created += 1
            elif action == "update":
                record = existing.get(target_date)
                if record is None or record.source != WorkScheduleRecord.Source.GENERATED:
                    conflicts += 1
                    continue
                expected = row["expected"]
                record.shift_type = expected["shiftType"]
                record.status = expected["status"]
                record.start_time = (
                    time.fromisoformat(expected["startTime"])
                    if expected["startTime"]
                    else None
                )
                record.end_time = (
                    time.fromisoformat(expected["endTime"])
                    if expected["endTime"]
                    else None
                )
                record.source = WorkScheduleRecord.Source.GENERATED
                record.save(
                    update_fields=[
                        "shift_type",
                        "status",
                        "start_time",
                        "end_time",
                        "source",
                        "updated_at",
                    ]
                )
                updated += 1
            elif action == "existing":
                unchanged += 1
            else:
                preserved += 1
                if action == "conflict":
                    conflicts += 1

    return JsonResponse(
        {
            "ok": True,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "preserved": preserved,
            "conflicts": conflicts,
            "records": [
                engine.record_payload(record)
                for record in WorkScheduleRecord.objects.filter(
                    owner=request.user, date__gte=start, date__lte=end
                ).order_by("date")
            ],
        }
    )


@login_required
@require_POST
def schedule_migrate_api(request):
    payload = _request_data(request)
    if payload is None:
        return _errors({"__all__": ["Invalid request body."]})
    incoming = payload.get("records")
    if not isinstance(incoming, list):
        return _errors({"records": ["Provide a list of local schedule records."]})
    if len(incoming) > 1000:
        return _errors({"records": ["Import at most 1000 records at a time."]})

    approved = _approved(request.user)
    imported = 0
    skipped = 0
    conflicts = []
    invalid = []
    with db_transaction.atomic():
        existing = {
            record.date: record
            for record in WorkScheduleRecord.objects.select_for_update().filter(
                owner=request.user
            )
        }
        for index, item in enumerate(incoming):
            try:
                values = _record_values(item, force_manual=True)
                if values["is_exception"] and not approved:
                    values["is_exception"] = False
                requested_source = item.get("source") if isinstance(item, dict) else None
                if requested_source == "generated":
                    if not approved:
                        invalid.append(
                            {
                                "index": index,
                                "date": item.get("date"),
                                "message": "Generated workplace records require approval.",
                            }
                        )
                        continue
                    values["source"] = WorkScheduleRecord.Source.GENERATED
            except (engine.RotationError, ValidationError) as error:
                invalid.append(
                    {
                        "index": index,
                        "date": item.get("date") if isinstance(item, dict) else "",
                        "message": str(error),
                    }
                )
                continue

            current = existing.get(values["date"])
            if current is not None:
                same = (
                    current.shift_type == values["shift_type"]
                    and current.status == values["status"]
                    and engine._time_text(current.start_time)
                    == engine._time_text(values["start_time"])
                    and engine._time_text(current.end_time)
                    == engine._time_text(values["end_time"])
                )
                if same:
                    skipped += 1
                else:
                    conflicts.append(
                        {
                            "date": values["date"].isoformat(),
                            "message": (
                                "A different server record already exists for this "
                                "date. The server record was kept."
                            ),
                            "server": engine.record_payload(current),
                        }
                    )
                continue

            record = WorkScheduleRecord(owner=request.user, **values)
            try:
                record.full_clean()
                record.save()
            except (ValidationError, IntegrityError) as error:
                invalid.append(
                    {
                        "index": index,
                        "date": values["date"].isoformat(),
                        "message": str(error),
                    }
                )
                continue
            existing[record.date] = record
            imported += 1

        config_imported = False
        config_conflicts = []
        incoming_config = payload.get("config")
        if approved and isinstance(incoming_config, dict):
            config_row = (
                WorkRotationConfig.objects.select_for_update()
                .filter(owner=request.user)
                .first()
            )
            if config_row is None or not config_row.configured:
                try:
                    config, anchor_date, anchor_shift, position = _parse_config(
                        incoming_config
                    )
                    temp = _temp_config(
                        request.user, config, anchor_date, anchor_shift, position
                    )
                    records = list(
                        WorkScheduleRecord.objects.filter(owner=request.user)
                    )
                    resolution = engine.resolve_rotation(records, temp)
                    if resolution["ready"]:
                        if config_row is None:
                            config_row = WorkRotationConfig(owner=request.user)
                        config_row.configured = True
                        config_row.working_days_per_cycle = config[
                            "working_days_per_cycle"
                        ]
                        config_row.off_days_per_cycle = config["off_days_per_cycle"]
                        config_row.shift_sequence = config["shift_sequence"]
                        config_row.shift_times = config["shift_times"]
                        config_row.anchor_date = anchor_date
                        config_row.anchor_shift = anchor_shift
                        config_row.anchor_position = position
                        config_row.save()
                        config_imported = True
                    else:
                        config_conflicts = resolution["issues"]
                except engine.RotationError as error:
                    config_conflicts = [str(error)]

    return JsonResponse(
        {
            "ok": True,
            "imported": imported,
            "skipped": skipped,
            "conflicts": conflicts,
            "invalid": invalid,
            "configImported": config_imported,
            "configConflicts": config_conflicts,
        }
    )
