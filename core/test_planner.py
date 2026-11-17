import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Account,
    DailyPlan,
    DailyPlanTopThree,
    DailyTask,
    Notification,
    Project,
    ProjectMilestone,
    Transaction,
    User,
)

PASSWORD = "StrongPass123!"


def post_json(client, url, payload):
    return client.post(
        url, data=json.dumps(payload), content_type="application/json"
    )


def patch_json(client, url, payload):
    return client.patch(
        url, data=json.dumps(payload), content_type="application/json"
    )


def put_json(client, url, payload):
    return client.put(
        url, data=json.dumps(payload), content_type="application/json"
    )


def make_user(email):
    return User.objects.create_user(email=email, password=PASSWORD)


def create_plan(client, day):
    response = post_json(
        client, reverse("core:daily_plan_create"), {"date": day.isoformat()}
    )
    assert response.status_code == 201, response.content
    return response.json()["dailyPlan"]


def add_task(client, plan_id, title="Task", **extra):
    payload = {"title": title, "priority": "medium"}
    payload.update(extra)
    response = post_json(
        client,
        reverse("core:daily_task_create", args=[plan_id]),
        payload,
    )
    assert response.status_code == 201, response.content
    return response.json()


def create_project(client, **overrides):
    today = date(2026, 11, 1)
    payload = {
        "name": "Alpha project",
        "objectives": "Ship the beta",
        "executionPlan": "Plan, build, review",
        "startDate": today.isoformat(),
        "endDate": (today + timedelta(days=30)).isoformat(),
        "deadline": (today + timedelta(days=30)).isoformat(),
        "estimatedCost": "250000.00",
        "status": "active",
        "notes": "Planning only",
    }
    payload.update(overrides)
    response = post_json(
        client, reverse("core:project_create"), payload
    )
    assert response.status_code == 201, response.content
    return response.json()["project"]


class DailyPlannerApiTests(TestCase):
    def setUp(self):
        self.user = make_user("planner@example.com")
        self.other = make_user("other@example.com")
        self.client.force_login(self.user)
        self.day = date(2026, 11, 12)

    def test_create_retrieve_and_duplicate_daily_plan(self):
        plan = create_plan(self.client, self.day)
        self.assertEqual(plan["date"], self.day.isoformat())
        self.assertEqual(plan["tasks"], [])
        duplicate = post_json(
            self.client,
            reverse("core:daily_plan_create"),
            {"date": self.day.isoformat()},
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_ensure_daily_plan_is_idempotent(self):
        first = post_json(
            self.client,
            reverse("core:daily_plan_ensure"),
            {"date": self.day.isoformat()},
        )
        second = post_json(
            self.client,
            reverse("core:daily_plan_ensure"),
            {"date": self.day.isoformat()},
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            first.json()["dailyPlan"]["id"], second.json()["dailyPlan"]["id"]
        )

    def test_tasks_order_completion_and_score(self):
        plan = create_plan(self.client, self.day)
        first = add_task(self.client, plan["id"], "First", priority="high")
        second = add_task(self.client, plan["id"], "Second")
        self.assertEqual(first["task"]["position"], 0)
        self.assertEqual(second["task"]["position"], 1)

        detail = self.client.get(
            reverse("core:daily_plan_detail", args=[plan["id"]])
        ).json()["dailyPlan"]
        self.assertEqual(
            [task["title"] for task in detail["tasks"]], ["First", "Second"]
        )
        self.assertTrue(detail["score"]["available"])
        self.assertEqual(detail["score"]["completedTasks"], 0)
        self.assertEqual(detail["score"]["totalTasks"], 2)
        self.assertEqual(detail["score"]["percentage"], 0.0)

        completed = patch_json(
            self.client,
            reverse(
                "core:daily_task_detail",
                args=[plan["id"], first["task"]["id"]],
            ),
            {"completed": True},
        )
        self.assertEqual(completed.status_code, 200)
        scored = completed.json()["dailyPlan"]["score"]
        self.assertTrue(scored["available"])
        self.assertEqual(scored["completedTasks"], 1)
        self.assertEqual(scored["totalTasks"], 2)
        self.assertEqual(scored["percentage"], 50.0)

        completed_second = patch_json(
            self.client,
            reverse(
                "core:daily_task_detail",
                args=[plan["id"], second["task"]["id"]],
            ),
            {"completed": True},
        )
        self.assertEqual(
            completed_second.json()["dailyPlan"]["score"]["percentage"], 100.0
        )

    def test_empty_plan_score_is_unavailable(self):
        plan = create_plan(self.client, self.day)
        detail = self.client.get(
            reverse("core:daily_plan_detail", args=[plan["id"]])
        ).json()["dailyPlan"]
        self.assertFalse(detail["score"]["available"])
        self.assertIsNone(detail["score"]["percentage"])
        self.assertEqual(detail["score"]["totalTasks"], 0)

    def test_top_three_references_existing_tasks_and_limit(self):
        plan = create_plan(self.client, self.day)
        tasks = [
            add_task(self.client, plan["id"], "Task %s" % index)["task"]
            for index in range(4)
        ]
        ids = [task["id"] for task in tasks[:3]]
        response = put_json(
            self.client,
            reverse("core:daily_top_three", args=[plan["id"]]),
            {"taskIds": ids},
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()["dailyPlan"]
        self.assertEqual(updated["topThreeTaskIds"], ids)
        self.assertTrue(all(task["isTopThree"] for task in updated["tasks"][:3]))
        self.assertFalse(updated["tasks"][3]["isTopThree"])
        self.assertEqual(
            DailyTask.objects.filter(plan_id=plan["id"]).count(), 4
        )

        too_many = put_json(
            self.client,
            reverse("core:daily_top_three", args=[plan["id"]]),
            {"taskIds": [task["id"] for task in tasks]},
        )
        self.assertEqual(too_many.status_code, 400)

        duplicate = put_json(
            self.client,
            reverse("core:daily_top_three", args=[plan["id"]]),
            {"taskIds": [ids[0], ids[0]]},
        )
        self.assertEqual(duplicate.status_code, 400)

    def test_top_three_rejects_other_plans_task(self):
        plan = create_plan(self.client, self.day)
        other_day = self.day + timedelta(days=1)
        other_plan = create_plan(self.client, other_day)
        own_task = add_task(self.client, plan["id"], "Own")["task"]
        foreign_task = add_task(self.client, other_plan["id"], "Foreign")["task"]
        response = put_json(
            self.client,
            reverse("core:daily_top_three", args=[plan["id"]]),
            {"taskIds": [own_task["id"], foreign_task["id"]]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DailyPlanTopThree.objects.filter(plan_id=plan["id"]).count(), 0)

    def test_deleting_task_removes_top_three_reference(self):
        plan = create_plan(self.client, self.day)
        task = add_task(self.client, plan["id"], "Important")["task"]
        put_json(
            self.client,
            reverse("core:daily_top_three", args=[plan["id"]]),
            {"taskIds": [task["id"]]},
        )
        response = self.client.delete(
            reverse(
                "core:daily_task_detail", args=[plan["id"], task["id"]]
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["dailyPlan"]["topThreeTaskIds"], []
        )
        self.assertEqual(DailyPlanTopThree.objects.count(), 0)

    def test_date_specific_isolation_and_conflict(self):
        first = create_plan(self.client, self.day)
        second = create_plan(self.client, self.day + timedelta(days=1))
        add_task(self.client, first["id"], "Only first")
        second_detail = self.client.get(
            reverse("core:daily_plan_detail", args=[second["id"]])
        ).json()["dailyPlan"]
        self.assertEqual(second_detail["tasks"], [])
        conflict = patch_json(
            self.client,
            reverse("core:daily_plan_detail", args=[second["id"]]),
            {"date": self.day.isoformat()},
        )
        self.assertEqual(conflict.status_code, 409)

    def test_notes_remarks_and_meetings_persist(self):
        plan = create_plan(self.client, self.day)
        response = patch_json(
            self.client,
            reverse("core:daily_plan_detail", args=[plan["id"]]),
            {"notes": "Daily note", "remarks": "Daily remark"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["dailyPlan"]["notes"], "Daily note")
        self.assertEqual(response.json()["dailyPlan"]["remarks"], "Daily remark")

        meeting = post_json(
            self.client,
            reverse("core:daily_meeting_create", args=[plan["id"]]),
            {
                "title": "Stand-up",
                "startTime": "09:00",
                "endTime": "09:30",
                "location": "Room 1",
                "notes": "Bring updates",
            },
        )
        self.assertEqual(meeting.status_code, 201)
        payload = meeting.json()["dailyPlan"]["meetings"][0]
        self.assertEqual(payload["date"], self.day.isoformat())
        self.assertEqual(payload["startTime"], "09:00")

        invalid = post_json(
            self.client,
            reverse("core:daily_meeting_create", args=[plan["id"]]),
            {
                "title": "Backwards",
                "startTime": "11:00",
                "endTime": "10:00",
            },
        )
        self.assertEqual(invalid.status_code, 400)

    def test_ownership_prevents_cross_user_access(self):
        plan = create_plan(self.client, self.day)
        task = add_task(self.client, plan["id"], "Private")["task"]
        project = create_project(self.client)

        self.client.force_login(self.other)
        snapshot = self.client.get(reverse("core:planner_api")).json()
        self.assertEqual(snapshot["dailyPlans"], [])
        self.assertEqual(snapshot["projects"], [])
        self.assertEqual(
            self.client.get(
                reverse("core:daily_plan_detail", args=[plan["id"]])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete(
                reverse("core:daily_task_detail", args=[plan["id"], task["id"]])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete(
                reverse("core:project_detail", args=[project["id"]])
            ).status_code,
            404,
        )

    def test_unauthenticated_requests_are_rejected(self):
        self.client.logout()
        response = self.client.get(reverse("core:planner_api"))
        self.assertIn(response.status_code, (302, 401, 403))
        self.assertNotEqual(response.status_code, 200)


class ProjectPlannerApiTests(TestCase):
    def setUp(self):
        self.user = make_user("projects@example.com")
        self.other = make_user("other-projects@example.com")
        self.client.force_login(self.user)
        self.today = date(2026, 11, 1)

    def test_project_create_update_delete(self):
        project = create_project(self.client)
        self.assertEqual(project["estimatedCost"], 250000.0)
        self.assertEqual(project["status"], "active")
        self.assertEqual(project["progress"]["totalMilestones"], 0)

        updated = patch_json(
            self.client,
            reverse("core:project_detail", args=[project["id"]]),
            {"name": "Renamed", "status": "on-hold"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["project"]["name"], "Renamed")
        self.assertEqual(updated.json()["project"]["status"], "on-hold")

        deleted = self.client.delete(
            reverse("core:project_detail", args=[project["id"]])
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Project.objects.filter(pk=project["id"]).exists())

    def test_milestones_and_progress(self):
        project = create_project(self.client)
        first = post_json(
            self.client,
            reverse("core:milestone_create", args=[project["id"]]),
            {
                "title": "Design",
                "dueDate": (self.today + timedelta(days=5)).isoformat(),
                "status": "pending",
            },
        )
        second = post_json(
            self.client,
            reverse("core:milestone_create", args=[project["id"]]),
            {
                "title": "Build",
                "dueDate": (self.today + timedelta(days=10)).isoformat(),
                "status": "in-progress",
            },
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        project_payload = second.json()["project"]
        self.assertEqual(project_payload["progress"]["completedMilestones"], 0)
        self.assertEqual(project_payload["progress"]["percentage"], 0.0)

        milestone_id = first.json()["milestone"]["id"]
        completed = patch_json(
            self.client,
            reverse(
                "core:milestone_detail", args=[project["id"], milestone_id]
            ),
            {"status": "completed"},
        )
        self.assertEqual(completed.status_code, 200)
        progress = completed.json()["project"]["progress"]
        self.assertEqual(progress["completedMilestones"], 1)
        self.assertEqual(progress["totalMilestones"], 2)
        self.assertEqual(progress["percentage"], 50.0)
        self.assertEqual(
            completed.json()["milestone"]["completionDate"],
            timezone.localdate().isoformat(),
        )

        deleted = self.client.delete(
            reverse(
                "core:milestone_detail", args=[project["id"], milestone_id]
            )
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            deleted.json()["project"]["progress"]["percentage"], 0.0
        )

    def test_project_date_validation(self):
        bad_end = create_project_payload = {
            "name": "Bad",
            "startDate": self.today.isoformat(),
            "endDate": (self.today - timedelta(days=1)).isoformat(),
            "deadline": (self.today + timedelta(days=5)).isoformat(),
            "estimatedCost": 0,
            "status": "planned",
        }
        response = post_json(
            self.client, reverse("core:project_create"), bad_end
        )
        self.assertEqual(response.status_code, 400)

        bad_deadline = dict(create_project_payload)
        bad_deadline.update(
            {
                "endDate": (self.today + timedelta(days=3)).isoformat(),
                "deadline": (self.today - timedelta(days=2)).isoformat(),
            }
        )
        response = post_json(
            self.client, reverse("core:project_create"), bad_deadline
        )
        self.assertEqual(response.status_code, 400)

    def test_milestone_dates_must_fit_project_range(self):
        project = create_project(self.client)
        before = post_json(
            self.client,
            reverse("core:milestone_create", args=[project["id"]]),
            {
                "title": "Too early",
                "dueDate": (self.today - timedelta(days=1)).isoformat(),
            },
        )
        self.assertEqual(before.status_code, 400)
        after = post_json(
            self.client,
            reverse("core:milestone_create", args=[project["id"]]),
            {
                "title": "Too late",
                "dueDate": (self.today + timedelta(days=60)).isoformat(),
            },
        )
        self.assertEqual(after.status_code, 400)

    def test_statuses_are_mutually_exclusive_counts(self):
        create_project(self.client, name="Active", status="active")
        create_project(self.client, name="Done", status="completed")
        create_project(self.client, name="Planned", status="planned")
        projects = self.client.get(reverse("core:planner_api")).json()["projects"]
        active = [item for item in projects if item["status"] == "active"]
        completed = [item for item in projects if item["status"] == "completed"]
        planned = [item for item in projects if item["status"] == "planned"]
        self.assertEqual(len(active), 1)
        self.assertEqual(len(completed), 1)
        self.assertEqual(len(planned), 1)
        self.assertEqual(len(active) + len(completed) + len(planned), len(projects))

    def test_large_project_collection_is_not_truncated(self):
        for index in range(65):
            Project.objects.create(
                owner=self.user,
                name="Project %02d" % index,
                start_date=self.today,
                end_date=self.today + timedelta(days=10),
                deadline=self.today + timedelta(days=10),
                estimated_cost=Decimal("0.00"),
                status="planned",
            )
        projects = self.client.get(reverse("core:planner_api")).json()["projects"]
        self.assertEqual(len(projects), 65)

    def test_estimated_costs_never_affect_financial_records(self):
        account = Account.objects.create(
            owner=self.user,
            name="Main",
            account_type=Account.AccountType.BANK,
            opening_balance=Decimal("1000.00"),
            opening_balance_date=self.today,
        )
        Transaction.objects.create(
            owner=self.user,
            transaction_type=Transaction.TransactionType.INCOME,
            account=account,
            amount=Decimal("500.00"),
            date=self.today,
        )
        before_transactions = Transaction.objects.count()
        before_balance = account.calculate_balance()
        project = create_project(
            self.client, estimatedCost="9999999.99", status="active"
        )
        post_json(
            self.client,
            reverse("core:milestone_create", args=[project["id"]]),
            {
                "title": "Expensive milestone",
                "dueDate": (self.today + timedelta(days=5)).isoformat(),
            },
        )
        self.assertEqual(Transaction.objects.count(), before_transactions)
        self.assertEqual(account.calculate_balance(), before_balance)
        project_record = Project.objects.get(pk=project["id"])
        self.assertEqual(project_record.estimated_cost, Decimal("9999999.99"))

    def test_project_ownership(self):
        project = create_project(self.client)
        milestone = post_json(
            self.client,
            reverse("core:milestone_create", args=[project["id"]]),
            {
                "title": "Private milestone",
                "dueDate": (self.today + timedelta(days=4)).isoformat(),
            },
        ).json()["milestone"]
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(
                reverse("core:project_detail", args=[project["id"]])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete(
                reverse(
                    "core:milestone_detail",
                    args=[project["id"], milestone["id"]],
                )
            ).status_code,
            404,
        )


class PlannerMigrationTests(TestCase):
    def setUp(self):
        self.user = make_user("migrate@example.com")
        self.client.force_login(self.user)
        self.today = date(2026, 11, 1)

    def test_local_plans_and_projects_migrate_without_duplicates(self):
        snapshot = {
            "dailyPlans": [
                {
                    "id": "day_local_1",
                    "date": self.today.isoformat(),
                    "dayStatus": "unspecified",
                    "notes": "Imported plan",
                    "remarks": "Imported remark",
                    "tasks": [
                        {
                            "id": "task_local_1",
                            "title": "Migrated task",
                            "description": "From local storage",
                            "completed": True,
                            "priority": "high",
                            "isTopThree": True,
                        }
                    ],
                    "meetings": [
                        {
                            "id": "meet_local_1",
                            "title": "Local appointment",
                            "date": self.today.isoformat(),
                            "startTime": "10:00",
                            "endTime": "11:00",
                            "location": "",
                            "notes": "",
                        }
                    ],
                    "topThreeTaskIds": ["task_local_1"],
                }
            ],
            "projects": [
                {
                    "id": "prj_local_1",
                    "name": "Migrated project",
                    "objectives": "",
                    "executionPlan": "",
                    "startDate": self.today.isoformat(),
                    "endDate": (self.today + timedelta(days=10)).isoformat(),
                    "deadline": (self.today + timedelta(days=10)).isoformat(),
                    "estimatedCost": 1000,
                    "status": "planned",
                    "notes": "",
                    "milestones": [
                        {
                            "id": "mile_local_1",
                            "title": "Imported milestone",
                            "description": "",
                            "dueDate": (self.today + timedelta(days=5)).isoformat(),
                            "status": "pending",
                            "completionDate": None,
                            "notes": "",
                        }
                    ],
                }
            ],
        }
        first = post_json(
            self.client, reverse("core:planner_migrate"), snapshot
        )
        self.assertEqual(first.status_code, 200)
        body = first.json()
        self.assertEqual(body["dailyPlansImported"], 1)
        self.assertEqual(body["projectsImported"], 1)

        plan = DailyPlan.objects.get(owner=self.user)
        self.assertEqual(plan.tasks.count(), 1)
        self.assertEqual(plan.top_three_entries.count(), 1)
        self.assertEqual(plan.meetings.count(), 1)
        self.assertEqual(plan.notes, "Imported plan")
        self.assertEqual(plan.remarks, "Imported remark")
        self.assertEqual(Project.objects.filter(owner=self.user).count(), 1)

        second = post_json(
            self.client, reverse("core:planner_migrate"), snapshot
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["dailyPlansSkipped"], 1)
        self.assertEqual(second.json()["projectsSkipped"], 1)
        self.assertEqual(DailyPlan.objects.filter(owner=self.user).count(), 1)
        self.assertEqual(Project.objects.filter(owner=self.user).count(), 1)

    def test_existing_server_plan_is_kept_on_conflict(self):
        plan = create_plan(self.client, self.today)
        add_task(self.client, plan["id"], "Server task")
        snapshot = {
            "dailyPlans": [
                {
                    "id": "day_local_conflict",
                    "date": self.today.isoformat(),
                    "dayStatus": "unspecified",
                    "tasks": [],
                    "meetings": [],
                    "topThreeTaskIds": [],
                }
            ],
            "projects": [],
        }
        response = post_json(
            self.client, reverse("core:planner_migrate"), snapshot
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["dailyPlansSkipped"], 1)
        self.assertEqual(
            response.json()["dailyPlanConflicts"][0]["date"],
            self.today.isoformat(),
        )
        self.assertEqual(
            DailyPlan.objects.get(owner=self.user).tasks.count(), 1
        )


class PlannerUiAndNotificationCorrectionTests(TestCase):
    def setUp(self):
        self.user = make_user("ui@example.com")
        self.client.force_login(self.user)

    def test_mobile_bottom_nav_uses_work_schedule_not_savings(self):
        response = self.client.get(reverse("core:planner"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        start = html.index('<nav class="bottom-nav')
        end = html.index("</nav>", start)
        bottom_nav = html[start:end]
        self.assertIn(reverse("core:work_schedule"), bottom_nav)
        self.assertIn("<span>Schedule</span>", bottom_nav)
        self.assertNotIn("Work Schedule", bottom_nav)
        self.assertNotIn(reverse("core:savings"), bottom_nav)

        script = (
            Path(__file__).resolve().parent.parent
            / "static"
            / "assets"
            / "js"
            / "global-navigation.js"
        ).read_text(encoding="utf-8")
        self.assertIn('label: "Schedule"', script)
        self.assertIn('href: "/work-schedule/"', script)
        self.assertNotIn('href: "/savings/"', script)

    def test_notification_destinations_follow_event_type(self):
        Notification.objects.create(
            owner=self.user,
            title="New Login",
            message="Signed in",
            event_type=Notification.EventType.LOGIN,
        )
        Notification.objects.create(
            owner=self.user,
            title="Welcome",
            message="Account created",
            event_type=Notification.EventType.WELCOME,
        )
        response = self.client.get(reverse("core:notifications_api"))
        self.assertEqual(response.status_code, 200)
        by_event = {
            item["eventType"]: item
            for item in response.json()["notifications"]
        }
        self.assertIsNone(by_event["login"]["destinationUrl"])
        self.assertEqual(
            by_event["welcome"]["destinationUrl"], reverse("core:settings")
        )

    def test_mobile_layout_and_notification_scroll_styles(self):
        root = Path(__file__).resolve().parent.parent
        nav_styles = (root / "static" / "assets" / "css" / "index.css").read_text(
            encoding="utf-8"
        )
        notification_styles = (
            root / "static" / "assets" / "css" / "notifications.css"
        ).read_text(encoding="utf-8")
        self.assertIn("min-height: 52px", nav_styles)
        self.assertIn("padding: 8px 0 0;", nav_styles)
        self.assertIn(
            '[data-page] .app-header {\n  border-top: 0;\n  background: var(--bg);\n}',
            nav_styles,
        )
        self.assertIn(
            "nav.bottom-nav {\n"
            "  padding-bottom: env(safe-area-inset-bottom, 0px);\n"
            "  background-color: var(--panel);\n"
            "}",
            nav_styles,
        )
        self.assertIn(
            "padding-bottom: calc(5.25rem + env(safe-area-inset-bottom, 0px))",
            nav_styles,
        )

        app_base = (
            root / "core" / "templates" / "layouts" / "app_base.html"
        ).read_text(encoding="utf-8")
        self.assertIn('class="app-header sticky top-0 glass', app_base)
        self.assertIn("overflow-y: auto", notification_styles)
        self.assertIn("100dvh - 7rem", notification_styles)
        self.assertIn("env(safe-area-inset-bottom", notification_styles)
