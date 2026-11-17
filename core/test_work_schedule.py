import json
from datetime import date, time, timedelta

from django.test import TestCase
from django.urls import reverse

from . import work_schedule as engine
from .models import User, WorkRotationConfig, WorkScheduleRecord

PASSWORD = "StrongPass123!"
MORNING = time(7, 0)
MORNING_END = time(13, 0)
AFTERNOON = time(13, 0)
AFTERNOON_END = time(19, 0)
NIGHT = time(19, 0)
NIGHT_END = time(7, 0)


def post_json(client, url, payload, method="post"):
    return getattr(client, method)(
        url,
        data=json.dumps(payload),
        content_type="application/json",
    )


def make_user(email, status=User.AccountStatus.APPROVED):
    return User.objects.create_user(
        email=email, password=PASSWORD, account_status=status
    )


def manual_record(
    owner,
    day,
    shift="morning",
    status="working",
    start=MORNING,
    end=MORNING_END,
    is_exception=False,
):
    if shift == "off":
        status = "off"
        start = None
        end = None
    return WorkScheduleRecord.objects.create(
        owner=owner,
        date=day,
        shift_type=shift,
        status=status,
        start_time=start,
        end_time=end,
        source=WorkScheduleRecord.Source.MANUAL,
        is_exception=is_exception,
    )


def add_block(owner, start_day, shift, start, end):
    for offset in range(3):
        manual_record(owner, start_day + timedelta(days=offset), shift, start=start, end=end)


def add_off(owner, start_day):
    for offset in range(2):
        manual_record(owner, start_day + timedelta(days=offset), "off")


def explicit_config(owner, anchor_day, shift, position=0):
    return WorkRotationConfig.objects.create(
        owner=owner,
        configured=True,
        working_days_per_cycle=3,
        off_days_per_cycle=2,
        shift_sequence=["morning", "afternoon", "night"],
        shift_times={
            "morning": {"start": "07:00", "end": "13:00"},
            "afternoon": {"start": "13:00", "end": "19:00"},
            "night": {"start": "19:00", "end": "07:00"},
        },
        anchor_date=anchor_day,
        anchor_shift=shift,
        anchor_position=position,
    )


class RotationSequenceTests(TestCase):
    def setUp(self):
        self.user = make_user("rotation@example.com")

    def expected(self, anchor_day, slot, day):
        config = engine.normalize_config({})
        return engine.expected_for_date(anchor_day, slot, day, config)

    def test_complete_fifteen_day_pattern_repeats_without_month_reset(self):
        anchor = date(2026, 10, 1)
        # Morning block (slot 0) for 3 days, off 2, afternoon 3, off 2,
        # night 3, off 2.
        expected = (
            [("morning", "working")] * 3
            + [("off", "off")] * 2
            + [("afternoon", "working")] * 3
            + [("off", "off")] * 2
            + [("night", "working")] * 3
            + [("off", "off")] * 2
            + [("morning", "working")] * 3
        )
        for offset, (shift, status) in enumerate(expected):
            result = self.expected(anchor, 0, anchor + timedelta(days=offset))
            self.assertEqual(result["shiftType"], shift, offset)
            self.assertEqual(result["status"], status, offset)

    def test_pattern_continues_across_year_boundary(self):
        anchor = date(2026, 12, 20)
        result = self.expected(anchor, 0, date(2027, 1, 5))
        # Dec 20-22 morning, 23-24 off, 25-27 afternoon, 28-29 off,
        # 30-Jan 1 night, Jan 2-3 off, Jan 4-6 morning.
        self.assertEqual(result["status"], "working")
        self.assertEqual(result["shiftType"], "morning")

    def test_night_shift_keeps_overnight_times(self):
        anchor = date(2026, 11, 5)
        result = self.expected(anchor, 10, anchor)
        self.assertEqual(result["shiftType"], "night")
        self.assertEqual(result["startTime"], "19:00")
        self.assertEqual(result["endTime"], "07:00")


class EmployeeAnchorDerivationTests(TestCase):
    def test_different_employees_derive_independent_positions(self):
        employees = [
            ("morning-a@example.com", date(2026, 10, 1), "morning", MORNING, MORNING_END),
            ("afternoon-b@example.com", date(2026, 11, 10), "afternoon", AFTERNOON, AFTERNOON_END),
            ("night-c@example.com", date(2026, 12, 1), "night", NIGHT, NIGHT_END),
        ]
        for email, start_day, shift, start, end in employees:
            owner = make_user(email)
            add_block(owner, start_day, shift, start, end)
            add_off(owner, start_day + timedelta(days=3))
            records = list(WorkScheduleRecord.objects.filter(owner=owner))
            resolution = engine.resolve_rotation(records, None)
            self.assertTrue(resolution["ready"], (email, resolution["issues"]))
            self.assertEqual(resolution["anchor"]["shiftType"], shift)
            following = start_day + timedelta(days=5)
            expected = engine.expected_for_date(
                date.fromisoformat(resolution["anchor"]["date"]),
                resolution["anchor"]["slot"],
                following,
                resolution["config"],
            )
            self.assertEqual(expected["status"], "working")
            self.assertNotEqual(expected["shiftType"], shift)

    def test_partial_working_block_and_off_period_can_establish_position(self):
        owner = make_user("partial@example.com")
        # Night block runs Nov 5-7; the employee records Nov 6-9.
        manual_record(owner, date(2026, 11, 6), "night", start=NIGHT, end=NIGHT_END)
        manual_record(owner, date(2026, 11, 7), "night", start=NIGHT, end=NIGHT_END)
        manual_record(owner, date(2026, 11, 8), "off")
        manual_record(owner, date(2026, 11, 9), "off")
        resolution = engine.resolve_rotation(
            list(WorkScheduleRecord.objects.filter(owner=owner)), None
        )
        self.assertTrue(resolution["ready"], resolution["issues"])
        anchor = resolution["anchor"]
        self.assertEqual(anchor["date"], "2026-11-06")
        self.assertEqual(anchor["shiftType"], "night")

    def test_off_day_anchor_uses_following_working_block(self):
        owner = make_user("off-anchor@example.com")
        manual_record(owner, date(2026, 11, 8), "off")
        manual_record(owner, date(2026, 11, 9), "off")
        add_block(
            owner, date(2026, 11, 10), "morning", MORNING, MORNING_END
        )
        resolution = engine.resolve_rotation(
            list(WorkScheduleRecord.objects.filter(owner=owner)), None
        )
        self.assertTrue(resolution["ready"], resolution["issues"])
        self.assertEqual(resolution["anchor"]["date"], "2026-11-08")
        self.assertEqual(resolution["anchor"]["shiftType"], "morning")

    def test_insufficient_records_do_not_guess_an_anchor(self):
        owner = make_user("insufficient@example.com")
        manual_record(owner, date(2026, 11, 1), "morning")
        resolution = engine.resolve_rotation(
            list(WorkScheduleRecord.objects.filter(owner=owner)), None
        )
        self.assertFalse(resolution["ready"])
        self.assertIn("unambiguous", " ".join(resolution["issues"]))

    def test_contradictory_records_stop_generation(self):
        owner = make_user("contradiction@example.com")
        add_block(owner, date(2026, 11, 1), "morning", MORNING, MORNING_END)
        add_off(owner, date(2026, 11, 4))
        manual_record(owner, date(2026, 11, 6), "night", start=NIGHT, end=NIGHT_END)
        resolution = engine.resolve_rotation(
            list(WorkScheduleRecord.objects.filter(owner=owner)), None
        )
        self.assertFalse(resolution["ready"])
        self.assertTrue(resolution["conflicts"])

    def test_exception_does_not_change_anchor(self):
        owner = make_user("exception@example.com")
        add_block(owner, date(2026, 11, 1), "morning", MORNING, MORNING_END)
        add_off(owner, date(2026, 11, 4))
        manual_record(
            owner,
            date(2026, 11, 6),
            "night",
            start=NIGHT,
            end=NIGHT_END,
            is_exception=True,
        )
        resolution = engine.resolve_rotation(
            list(WorkScheduleRecord.objects.filter(owner=owner)), None
        )
        self.assertTrue(resolution["ready"], resolution["issues"])
        self.assertEqual(resolution["anchor"]["date"], "2026-11-01")


class DeveloperFixtureTests(TestCase):
    """The November 2026 example is an isolated fixture, never a default."""

    def setUp(self):
        self.user = make_user("fixture@example.com")
        explicit_config(self.user, date(2026, 10, 31), "afternoon", 0)

    def expected(self, day):
        return engine.preview_rows(
            [], WorkRotationConfig.objects.get(owner=self.user), day, day
        )[1][0]["expected"]

    def test_fixture_continuation(self):
        cases = {
            date(2026, 10, 31): ("afternoon", "working"),
            date(2026, 11, 2): ("afternoon", "working"),
            date(2026, 11, 3): ("off", "off"),
            date(2026, 11, 5): ("night", "working"),
            date(2026, 11, 10): ("morning", "working"),
            date(2026, 11, 15): ("afternoon", "working"),
            date(2026, 11, 20): ("night", "working"),
            date(2026, 11, 25): ("morning", "working"),
            date(2026, 11, 30): ("afternoon", "working"),
            date(2026, 12, 2): ("afternoon", "working"),
        }
        for day, (shift, status) in cases.items():
            result = self.expected(day)
            self.assertEqual((result["shiftType"], result["status"]), (shift, status), day)

    def test_fixture_does_not_affect_another_employee(self):
        other = make_user("other-fixture@example.com")
        explicit_config(other, date(2026, 10, 31), "morning", 0)
        result = engine.preview_rows(
            [], WorkRotationConfig.objects.get(owner=other), date(2026, 11, 5), date(2026, 11, 5)
        )[1][0]["expected"]
        self.assertEqual(result["shiftType"], "afternoon")


class WorkScheduleApiTests(TestCase):
    def setUp(self):
        self.user = make_user("worker@example.com")
        self.other = make_user("other@example.com")
        self.client.force_login(self.user)

    def create_entry(self, day, shift="morning", start="07:00", end="13:00", **extra):
        payload = {
            "date": day,
            "shiftType": shift,
            "status": "off" if shift == "off" else "working",
            "startTime": "" if shift == "off" else start,
            "endTime": "" if shift == "off" else end,
        }
        payload.update(extra)
        return post_json(self.client, reverse("core:work_schedule_record_create"), payload)

    def test_approved_user_create_update_delete_and_isolation(self):
        created = self.create_entry("2026-11-01")
        self.assertEqual(created.status_code, 201, created.content)
        record_id = created.json()["record"]["id"]
        self.assertEqual(created.json()["record"]["source"], "manual")

        listed = self.client.get(reverse("core:work_schedule_api"))
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["records"]), 1)
        self.assertNotIn("other@example.com", json.dumps(listed.json()))

        blocked = self.client.delete(
            reverse("core:work_schedule_record_detail", args=[record_id])
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertFalse(WorkScheduleRecord.objects.filter(pk=record_id).exists())

    def test_other_users_record_cannot_be_touched(self):
        foreign = manual_record(self.other, date(2026, 11, 1))
        response = self.client.patch(
            reverse("core:work_schedule_record_detail", args=[foreign.pk]),
            data=json.dumps({"notes": "hijack"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.notes, "")

    def test_duplicate_date_is_rejected(self):
        self.create_entry("2026-11-01")
        duplicate = self.create_entry("2026-11-01", "night", "19:00", "07:00")
        self.assertEqual(duplicate.status_code, 409)

    def test_general_and_pending_users_cannot_configure_or_generate(self):
        for status, email in (
            (User.AccountStatus.GENERAL, "general@example.com"),
            (User.AccountStatus.PENDING, "pending@example.com"),
        ):
            user = make_user(email, status)
            self.client.force_login(user)
            payload = {
                "rotation": {
                    "workingDaysPerCycle": 3,
                    "offDaysPerCycle": 2,
                    "shiftSequence": ["morning", "afternoon", "night"],
                    "shiftTimes": {
                        "morning": {"start": "07:00", "end": "13:00"},
                        "afternoon": {"start": "13:00", "end": "19:00"},
                        "night": {"start": "19:00", "end": "07:00"},
                    },
                },
                "anchor": {"date": "2026-11-01", "shiftType": "morning", "cyclePosition": 0},
            }
            configured = post_json(
                self.client, reverse("core:work_schedule_config_api"), payload, "put"
            )
            self.assertEqual(configured.status_code, 403)
            preview = post_json(
                self.client,
                reverse("core:work_schedule_preview_api"),
                {"start": "2026-11-01", "end": "2026-11-05"},
            )
            self.assertEqual(preview.status_code, 403)
            listed = self.client.get(reverse("core:work_schedule_api"))
            self.assertFalse(listed.json()["approved"])
            self.assertFalse(listed.json()["readiness"]["canGenerate"])

    def test_preview_does_not_write_and_confirm_is_atomic(self):
        config_payload = {
            "rotation": {
                "workingDaysPerCycle": 3,
                "offDaysPerCycle": 2,
                "shiftSequence": ["morning", "afternoon", "night"],
                "shiftTimes": {
                    "morning": {"start": "07:00", "end": "13:00"},
                    "afternoon": {"start": "13:00", "end": "19:00"},
                    "night": {"start": "19:00", "end": "07:00"},
                },
            },
            "anchor": {"date": "2026-11-01", "shiftType": "morning", "cyclePosition": 0},
        }
        configured = post_json(
            self.client, reverse("core:work_schedule_config_api"), config_payload, "put"
        )
        self.assertEqual(configured.status_code, 200, configured.content)

        preview = post_json(
            self.client,
            reverse("core:work_schedule_preview_api"),
            {"start": "2026-11-01", "end": "2026-11-05"},
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(WorkScheduleRecord.objects.filter(owner=self.user).count(), 0)
        token = preview.json()["token"]

        confirmed = post_json(
            self.client,
            reverse("core:work_schedule_confirm_api"),
            {"start": "2026-11-01", "end": "2026-11-05", "token": token},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(confirmed.json()["created"], 5)
        self.assertEqual(WorkScheduleRecord.objects.filter(owner=self.user).count(), 5)
        self.assertTrue(
            WorkScheduleRecord.objects.filter(
                owner=self.user, source=WorkScheduleRecord.Source.GENERATED
            ).exists()
        )

        repeated = post_json(
            self.client,
            reverse("core:work_schedule_confirm_api"),
            {"start": "2026-11-01", "end": "2026-11-05", "token": token},
        )
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(repeated.json()["code"], "stale_preview")
        self.assertEqual(WorkScheduleRecord.objects.filter(owner=self.user).count(), 5)

    def test_manual_conflict_is_preserved_and_stale_preview_rejected(self):
        explicit_config(self.user, date(2026, 11, 1), "morning", 0)
        manual_record(
            self.user,
            date(2026, 11, 2),
            "night",
            start=NIGHT,
            end=NIGHT_END,
        )
        preview = post_json(
            self.client,
            reverse("core:work_schedule_preview_api"),
            {"start": "2026-11-01", "end": "2026-11-05"},
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        summary = preview.json()["summary"]
        self.assertEqual(summary["conflict"], 1)
        self.assertEqual(WorkScheduleRecord.objects.filter(owner=self.user).count(), 1)
        token = preview.json()["token"]

        # A new entry after the preview makes the old token stale.
        self.create_entry("2026-11-03", "afternoon", "13:00", "19:00")
        stale = post_json(
            self.client,
            reverse("core:work_schedule_confirm_api"),
            {"start": "2026-11-01", "end": "2026-11-05", "token": token},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "stale_preview")

    def test_editing_generated_entry_converts_it_to_manual(self):
        explicit_config(self.user, date(2026, 11, 1), "morning", 0)
        preview = post_json(
            self.client,
            reverse("core:work_schedule_preview_api"),
            {"start": "2026-11-01", "end": "2026-11-01"},
        )
        token = preview.json()["token"]
        post_json(
            self.client,
            reverse("core:work_schedule_confirm_api"),
            {"start": "2026-11-01", "end": "2026-11-01", "token": token},
        )
        record = WorkScheduleRecord.objects.get(owner=self.user)
        changed = post_json(
            self.client,
            reverse("core:work_schedule_record_detail", args=[record.pk]),
            {"shiftType": "night", "startTime": "19:00", "endTime": "07:00"},
            "patch",
        )
        self.assertEqual(changed.status_code, 200, changed.content)
        record.refresh_from_db()
        self.assertEqual(record.source, WorkScheduleRecord.Source.MANUAL)
        self.assertEqual(record.shift_type, "night")

    def test_night_times_are_preserved(self):
        created = self.create_entry(
            "2026-11-05", "night", "19:00", "07:00"
        )
        self.assertEqual(created.status_code, 201, created.content)
        record = WorkScheduleRecord.objects.get(owner=self.user)
        self.assertEqual(record.start_time, time(19, 0))
        self.assertEqual(record.end_time, time(7, 0))


class MigrationApiTests(TestCase):
    def setUp(self):
        self.user = make_user("migrate@example.com")
        self.client.force_login(self.user)

    def test_migration_is_idempotent_and_never_overwrites_conflicts(self):
        payload = {
            "records": [
                {
                    "date": "2026-11-01",
                    "shiftType": "morning",
                    "status": "working",
                    "startTime": "07:00",
                    "endTime": "13:00",
                    "source": "manual",
                },
                {
                    "date": "2026-11-02",
                    "shiftType": "afternoon",
                    "status": "working",
                    "startTime": "13:00",
                    "endTime": "19:00",
                    "source": "manual",
                },
            ]
        }
        first = post_json(self.client, reverse("core:work_schedule_migrate_api"), payload)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["imported"], 2)

        second = post_json(self.client, reverse("core:work_schedule_migrate_api"), payload)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["imported"], 0)
        self.assertEqual(second.json()["skipped"], 2)
        self.assertEqual(WorkScheduleRecord.objects.filter(owner=self.user).count(), 2)

        conflicting = {
            "records": [
                {
                    "date": "2026-11-01",
                    "shiftType": "night",
                    "status": "working",
                    "startTime": "19:00",
                    "endTime": "07:00",
                }
            ]
        }
        result = post_json(
            self.client, reverse("core:work_schedule_migrate_api"), conflicting
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json()["conflicts"]), 1)
        kept = WorkScheduleRecord.objects.get(owner=self.user, date=date(2026, 11, 1))
        self.assertEqual(kept.shift_type, "morning")

    def test_migration_accepts_unassigned_entry_without_times(self):
        payload = {
            "records": [
                {
                    "date": "2026-11-03",
                    "shiftType": "morning",
                    "status": "unassigned",
                    "source": "manual",
                }
            ]
        }
        response = post_json(
            self.client, reverse("core:work_schedule_migrate_api"), payload
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["imported"], 1)
        record = WorkScheduleRecord.objects.get(owner=self.user)
        self.assertEqual(record.status, WorkScheduleRecord.Status.UNASSIGNED)
        self.assertIsNone(record.start_time)


class WorkSchedulePageTests(TestCase):
    def test_approved_page_exposes_backend_urls(self):
        user = make_user("page-approved@example.com")
        self.client.force_login(user)
        response = self.client.get(reverse("core:work_schedule"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("core:work_schedule_api"))
        self.assertContains(response, "Automatic schedule generation")

    def test_general_page_hides_workplace_rotation_controls(self):
        user = make_user("page-general@example.com", User.AccountStatus.GENERAL)
        self.client.force_login(user)
        response = self.client.get(reverse("core:work_schedule"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("core:work_schedule_api"))
        self.assertNotContains(response, "Automatic schedule generation")
