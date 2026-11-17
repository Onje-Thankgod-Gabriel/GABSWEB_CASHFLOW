"""Reusable, server-side rotation engine for Work Schedule.

The engine is deliberately independent of views and of any particular
employee. Callers pass the employee's own confirmed records and, when the
employee has saved one, their own explicit rotation setup. Nothing in this
module writes to the database.
"""

from datetime import date, timedelta

SHIFT_MORNING = "morning"
SHIFT_AFTERNOON = "afternoon"
SHIFT_NIGHT = "night"
SHIFT_OFF = "off"
WORKING_SHIFTS = (SHIFT_MORNING, SHIFT_AFTERNOON, SHIFT_NIGHT)
VALID_SHIFTS = WORKING_SHIFTS + (SHIFT_OFF,)
DEFAULT_SEQUENCE = list(WORKING_SHIFTS)
DEFAULT_TIMES = {
    SHIFT_MORNING: {"start": "07:00", "end": "13:00"},
    SHIFT_AFTERNOON: {"start": "13:00", "end": "19:00"},
    SHIFT_NIGHT: {"start": "19:00", "end": "07:00"},
}
MAX_PREVIEW_DAYS = 400


class RotationError(ValueError):
    """Raised when rotation settings are invalid."""


class RotationNotReady(Exception):
    """Raised when the employee's rotation position is not unambiguous."""

    def __init__(self, issues, conflicts=None, resolution=None):
        self.issues = list(issues or [])
        self.conflicts = list(conflicts or [])
        self.resolution = resolution or {}
        super().__init__(self.issues[0] if self.issues else "Rotation is not ready.")


def _as_int(value, default, label, minimum=1, maximum=14):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise RotationError(f"{label} must be a whole number.")
    if number < minimum or number > maximum:
        raise RotationError(
            f"{label} must be a whole number from {minimum} to {maximum}."
        )
    return number


def _valid_time(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    try:
        hours, minutes = (int(part) for part in value.split(":"))
    except (TypeError, ValueError):
        return False
    return 0 <= hours <= 23 and 0 <= minutes <= 59


def normalize_config(source):
    """Return a validated plain-dict rotation configuration."""
    if isinstance(source, dict):
        get = source.get
    else:
        get = lambda key, default=None: getattr(source, key, default)
    working = _as_int(
        get("working_days_per_cycle", 3), 3, "Working days per cycle"
    )
    off = _as_int(get("off_days_per_cycle", 2), 2, "Days off per cycle")
    if working + off > 28:
        raise RotationError("The work/off cycle cannot exceed 28 days.")

    sequence = get("shift_sequence", None) or DEFAULT_SEQUENCE
    if not isinstance(sequence, (list, tuple)):
        raise RotationError("Shift sequence must be a list of shifts.")
    sequence = [str(item).strip().lower() for item in sequence]
    if not sequence or len(sequence) > 6:
        raise RotationError("Shift sequence must contain between 1 and 6 shifts.")
    if any(item not in WORKING_SHIFTS for item in sequence):
        raise RotationError(
            "Shift sequence may only contain morning, afternoon or night."
        )
    if len(set(sequence)) != len(sequence):
        raise RotationError("Shift sequence cannot repeat the same shift.")

    raw_times = get("shift_times", None) or {}
    if not isinstance(raw_times, dict):
        raise RotationError("Shift times must be an object.")
    times = {}
    for shift in WORKING_SHIFTS:
        value = raw_times.get(shift) or DEFAULT_TIMES[shift]
        if not isinstance(value, dict):
            raise RotationError(f"{shift.title()} times must be an object.")
        start = value.get("start") or DEFAULT_TIMES[shift]["start"]
        end = value.get("end") or DEFAULT_TIMES[shift]["end"]
        if not _valid_time(start) or not _valid_time(end):
            raise RotationError(f"Enter a valid start and end time for {shift}.")
        if start == end:
            raise RotationError(f"{shift.title()} start and end times cannot match.")
        times[shift] = {"start": start, "end": end}

    return {
        "working_days_per_cycle": working,
        "off_days_per_cycle": off,
        "shift_sequence": sequence,
        "shift_times": times,
        "block_length": working + off,
        "cycle_length": (working + off) * len(sequence),
    }


def config_from_instance(instance):
    if instance is None:
        return normalize_config({})
    return normalize_config(
        {
            "working_days_per_cycle": instance.working_days_per_cycle,
            "off_days_per_cycle": instance.off_days_per_cycle,
            "shift_sequence": instance.shift_sequence,
            "shift_times": instance.shift_times,
        }
    )


def _time_text(value):
    if value is None:
        return ""
    return value.strftime("%H:%M") if hasattr(value, "strftime") else str(value)[:5]


def record_payload(record):
    return {
        "id": str(record.pk),
        "date": record.date.isoformat(),
        "shiftType": record.shift_type,
        "status": record.status,
        "startTime": _time_text(record.start_time),
        "endTime": _time_text(record.end_time),
        "team": record.team,
        "notes": record.notes,
        "source": record.source,
        "isException": bool(record.is_exception),
        "createdAt": record.created_at.isoformat() if record.created_at else "",
        "updatedAt": record.updated_at.isoformat() if record.updated_at else "",
    }


def expected_for_date(anchor_date, slot, target_date, config):
    """Return the expected schedule for one date from an absolute cycle slot."""
    block = config["block_length"]
    sequence = config["shift_sequence"]
    global_slot = int(slot) + (target_date - anchor_date).days
    block_number, within = divmod(global_slot, block)
    shift = sequence[block_number % len(sequence)]
    if within < config["working_days_per_cycle"]:
        times = config["shift_times"][shift]
        return {
            "date": target_date.isoformat(),
            "status": "working",
            "shiftType": shift,
            "startTime": times["start"],
            "endTime": times["end"],
            "position": within,
            "block": block_number,
            "source": "calculated",
        }
    return {
        "date": target_date.isoformat(),
        "status": "off",
        "shiftType": SHIFT_OFF,
        "startTime": "",
        "endTime": "",
        "position": within,
        "block": block_number,
        "source": "calculated",
    }


def anchor_from_config(config_row):
    """Convert a saved WorkRotationConfig row to engine anchor fields."""
    if config_row is None or not config_row.configured:
        return None, ["Save your rotation reference before generating."]
    if not config_row.anchor_date or not config_row.anchor_shift:
        return None, ["Add a rotation reference date and shift block."]
    config = config_from_instance(config_row)
    sequence = config["shift_sequence"]
    shift = (config_row.anchor_shift or "").strip().lower()
    if shift not in sequence:
        return None, ["The reference shift is not part of your shift sequence."]
    block = config["block_length"]
    position = int(config_row.anchor_position or 0)
    if position < 0 or position >= block:
        return None, ["The reference position is outside the work/off cycle."]
    shift_index = sequence.index(shift)
    if position < config["working_days_per_cycle"]:
        slot = shift_index * block + position
    else:
        slot = ((shift_index - 1) % len(sequence)) * block + position
    return {
        "date": config_row.anchor_date,
        "slot": slot,
        "position": position,
        "shiftType": shift,
        "startTime": config["shift_times"][shift]["start"],
        "endTime": config["shift_times"][shift]["end"],
    }, []


def anchor_payload(anchor, config):
    if not anchor:
        return None
    current = expected_for_date(anchor["date"], anchor["slot"], anchor["date"], config)
    if current["status"] == "working":
        shift = current["shiftType"]
    else:
        block = config["block_length"]
        following = expected_for_date(
            anchor["date"],
            anchor["slot"],
            anchor["date"] + timedelta(days=block - current["position"]),
            config,
        )
        shift = following["shiftType"]
    return {
        "date": anchor["date"].isoformat(),
        "slot": anchor["slot"],
        "position": anchor["slot"] % config["block_length"],
        "shiftType": shift,
        "startTime": config["shift_times"][shift]["start"],
        "endTime": config["shift_times"][shift]["end"],
    }


def _matches_expected(record, expected, compare_times=False):
    if record.status != expected["status"]:
        return False
    if expected["status"] == "off":
        return True
    if record.shift_type != expected["shiftType"]:
        return False
    if compare_times and (record.start_time or record.end_time):
        if _time_text(record.start_time) != expected["startTime"]:
            return False
        if _time_text(record.end_time) != expected["endTime"]:
            return False
    return True


def _signature(reference_date, slot, config):
    return tuple(
        (item["status"], item["shiftType"])
        for item in (
            expected_for_date(
                reference_date, slot, reference_date + timedelta(days=index), config
            )
            for index in range(config["cycle_length"])
        )
    )


def _conflict_payload(record, expected):
    return {
        "date": record.date.isoformat(),
        "record": record_payload(record),
        "expected": expected,
    }


def resolve_rotation(records, config_row):
    """Resolve an employee's effective anchor and readiness.

    Explicit saved configuration is validated first. If it is absent or
    incomplete, only the employee's own manual, non-exception records are used
    to derive the position. Contradictions never silently move the anchor.
    """
    config = config_from_instance(config_row)
    manual = [
        record
        for record in records
        if record.source == "manual"
        and not record.is_exception
        and record.status in ("working", "off")
    ]

    explicit_anchor, anchor_issues = anchor_from_config(config_row)
    if anchor_issues and config_row is not None and config_row.configured:
        return {
            "ready": False,
            "source": "explicit",
            "config": config,
            "anchor": None,
            "issues": anchor_issues,
            "conflicts": [],
        }

    if explicit_anchor is not None:
        conflicts = []
        for record in manual:
            expected = expected_for_date(
                explicit_anchor["date"],
                explicit_anchor["slot"],
                record.date,
                config,
            )
            if not _matches_expected(record, expected):
                conflicts.append(_conflict_payload(record, expected))
        return {
            "ready": True,
            "source": "explicit",
            "config": config,
            "anchor": anchor_payload(explicit_anchor, config),
            "issues": [],
            "conflicts": conflicts,
        }

    if not manual:
        return {
            "ready": False,
            "source": None,
            "config": config,
            "anchor": None,
            "issues": [
                "Add confirmed working and off-day entries before the rotation "
                "position can be established."
            ],
            "conflicts": [],
            "resolution": {},
        }

    reference = min(record.date for record in manual)
    candidates = []
    for slot in range(config["cycle_length"]):
        if all(
            _matches_expected(
                record,
                expected_for_date(reference, slot, record.date, config),
            )
            for record in manual
        ):
            candidates.append(slot)

    if candidates:
        signatures = {}
        for slot in candidates:
            signatures.setdefault(_signature(reference, slot, config), []).append(slot)
        if len(signatures) == 1:
            slot = min(candidates)
            anchor = {
                "date": reference,
                "slot": slot,
                "position": slot % config["block_length"],
            }
            return {
                "ready": True,
                "source": "records",
                "config": config,
                "anchor": anchor_payload(anchor, config),
                "issues": [],
                "conflicts": [],
            }
        required_days = config["block_length"]
        return {
            "ready": False,
            "source": "records",
            "config": config,
            "anchor": None,
            "issues": [
                "The confirmed entries do not yet establish one unambiguous "
                "cycle position. Add at least "
                f"{required_days} consecutive confirmed days that include a full "
                "working block and the days off that follow it."
            ],
            "conflicts": [],
            "resolution": {
                "candidateCount": len(signatures),
                "suggestedDays": required_days,
            },
        }

    best_slot = None
    best_matches = -1
    best_conflicts = []
    for slot in range(config["cycle_length"]):
        conflicts = []
        matches = 0
        for record in manual:
            expected = expected_for_date(reference, slot, record.date, config)
            if _matches_expected(record, expected):
                matches += 1
            else:
                conflicts.append(_conflict_payload(record, expected))
        if matches > best_matches or (
            matches == best_matches and len(conflicts) < len(best_conflicts)
        ):
            best_slot, best_matches, best_conflicts = slot, matches, conflicts

    return {
        "ready": False,
        "source": "records",
        "config": config,
        "anchor": None,
        "issues": [
            "Your confirmed entries contain assignments that cannot all belong "
            "to one continuous rotation. Mark genuine one-off exceptions, or "
            "correct the conflicting entries."
        ],
        "conflicts": best_conflicts,
        "resolution": {"suggestedSlot": best_slot},
    }


def date_range(start, end):
    if end < start:
        raise RotationError("The end date must be on or after the start date.")
    count = (end - start).days + 1
    if count > MAX_PREVIEW_DAYS:
        raise RotationError(
            f"Choose a date range of {MAX_PREVIEW_DAYS} days or fewer."
        )
    return [start + timedelta(days=index) for index in range(count)]


def preview_rows(records, config_row, start, end):
    """Return preview rows without touching the database."""
    resolution = resolve_rotation(records, config_row)
    if not resolution["ready"]:
        raise RotationNotReady(
            resolution["issues"],
            resolution.get("conflicts"),
            resolution.get("resolution"),
        )
    anchor = resolution["anchor"]
    anchor_date = date.fromisoformat(anchor["date"])
    slot = anchor["slot"]
    config = resolution["config"]
    existing = {record.date: record for record in records}
    rows = []
    for target in date_range(start, end):
        expected = expected_for_date(anchor_date, slot, target, config)
        current = existing.get(target)
        if current is None:
            action = "create"
        elif current.source == "generated":
            action = (
                "existing"
                if _matches_expected(current, expected, compare_times=True)
                else "update"
            )
        elif current.is_exception:
            action = "preserved"
        elif _matches_expected(current, expected, compare_times=True):
            action = "preserved"
        else:
            action = "conflict"
        rows.append(
            {
                "date": target.isoformat(),
                "expected": dict(expected, source="generated", team="", notes=""),
                "existing": record_payload(current) if current else None,
                "action": action,
            }
        )
    return resolution, rows


def preview_summary(rows):
    result = {
        "create": 0,
        "update": 0,
        "existing": 0,
        "preserved": 0,
        "conflict": 0,
    }
    for row in rows:
        result[row["action"]] = result.get(row["action"], 0) + 1
    return result
