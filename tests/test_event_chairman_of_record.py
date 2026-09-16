import ast
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import selectinload
from sqlmodel import select

from app.models import Event, EventAttendance, EventChairAssignment, User


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"
TEMPLATE = ROOT / "app" / "templates" / "events_menu.html"
MIGRATION = (
    ROOT
    / "alembic"
    / "versions"
    / "9d7f3b1a6c20_add_event_chair_assignments.py"
)

ATTENDANCE_MIGRATION = (
    ROOT
    / "alembic"
    / "versions"
    / "4b83d27e91af_add_event_attendance.py"
)


def load_functions(names, namespace):
    source = MAIN.read_text()
    tree = ast.parse(source)

    nodes = []
    wanted = set(names)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            node.decorator_list = []
            nodes.append(node)

    found = {node.name for node in nodes}
    missing = wanted - found
    if missing:
        raise AssertionError(f"Functions not found: {sorted(missing)}")

    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            *nodes,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)

    exec(compile(module, str(MAIN), "exec"), namespace)
    return namespace


class FakeResult:
    def __init__(self, value):
        self.value = value

    def first(self):
        if isinstance(self.value, list):
            return self.value[0] if self.value else None
        return self.value

    def all(self):
        if isinstance(self.value, list):
            return self.value
        if self.value is None:
            return []
        return [self.value]


class FakeSession:
    def __init__(self, results=()):
        self.results = list(results)
        self.added = []
        self.commit_count = 0
        self.refresh_count = 0
        self.next_id = 900

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def exec(self, statement):
        if not self.results:
            raise AssertionError(f"Unexpected DB query: {statement}")
        return FakeResult(self.results.pop(0))

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commit_count += 1

    def refresh(self, value):
        self.refresh_count += 1
        if getattr(value, "id", None) is None:
            value.id = self.next_id
            self.next_id += 1


class TemplateRecorder:
    def TemplateResponse(self, request, template_name, context, **kwargs):
        return {
            "template_name": template_name,
            "context": context,
        }


class ChairmanOfRecordTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 17, 1, 0, tzinfo=timezone.utc)

        self.admin = SimpleNamespace(
            id=1,
            handle="admin",
            role_names={"admin"},
        )
        self.president = SimpleNamespace(
            id=2,
            handle="president",
            role_names={"president"},
        )
        self.chair = SimpleNamespace(
            id=3,
            handle="chair",
            role_names={"chairman"},
        )
        self.chair2 = SimpleNamespace(
            id=4,
            handle="chair2",
            role_names={"chairman"},
        )
        self.member = SimpleNamespace(
            id=5,
            handle="member",
            role_names={"member"},
        )

        self.event = SimpleNamespace(
            id=41,
            title="Research Deliberation",
            starts_at=self.now,
            ends_at=None,
            stages=[],
        )

        self.actor = self.president
        self.db = None
        self.slog = Mock()

        def has_role(user, role):
            return role in getattr(user, "role_names", set())

        def effective_flags(user):
            return {
                "IS_ADMIN": has_role(user, "admin"),
                "IS_PRESIDENT": has_role(user, "president"),
                "IS_CHAIR": has_role(user, "chairman"),
                "IS_MEMBER": has_role(user, "member"),
            }

        namespace = {
            "Form": Form,
            "HTTPException": HTTPException,
            "RedirectResponse": RedirectResponse,
            "select": select,
            "selectinload": selectinload,
            "Event": Event,
            "User": User,
            "EventAttendance": EventAttendance,
            "EventChairAssignment": EventChairAssignment,
            "current_user": lambda request: self.actor,
            "effective_flags": effective_flags,
            "has_role": has_role,
            "get_session": lambda: self.db,
            "_require_event_access": lambda db, user, event_id: self.event,
            "_now_utc": lambda: self.now,
            "slog": self.slog,
            "urlencode": lambda values: "",
            "to_iso_z": lambda value: value.isoformat() if value else None,
            "templates": TemplateRecorder(),
        }

        self.ns = load_functions(
            {
                "_current_event_attendance",
                "_touch_event_attendance",
                "acknowledge_event_entry",
                "_current_event_chair_assignment",
                "assign_chairman_of_record",
                "acknowledge_chairman_of_record",
                "event_menu",
            },
            namespace,
        )

        self.real_touch_event_attendance = self.ns["_touch_event_attendance"]

        # Event-menu tests focus on rendered context. Dedicated tests below
        # exercise the real attendance creation/update helper.
        self.ns["_touch_event_attendance"] = (
            lambda db, event_id, user_id: (
                self.attendance(user_id=user_id),
                False,
            )
        )

    def attendance(
        self,
        *,
        ident=200,
        user_id=None,
        acknowledged=None,
        first_seen=None,
        last_seen=None,
    ):
        if user_id is None:
            user_id = self.member.id

        if first_seen is None:
            first_seen = self.now

        if last_seen is None:
            last_seen = first_seen

        return EventAttendance(
            id=ident,
            event_id=self.event.id,
            user_id=user_id,
            first_seen_at=first_seen,
            last_seen_at=last_seen,
            acknowledged_at=acknowledged,
        )

    def assignment(
        self,
        *,
        ident=100,
        chairman=3,
        assigned_by=2,
        acknowledged=None,
        ended=None,
    ):
        return EventChairAssignment(
            id=ident,
            event_id=self.event.id,
            chairman_user_id=chairman,
            assigned_by_id=assigned_by,
            assigned_at=self.now,
            acknowledged_at=acknowledged,
            ended_at=ended,
        )

    def test_president_can_make_first_assignment_and_semantic_log_is_minimal(self):
        self.actor = self.president
        self.db = FakeSession([
            self.event,
            self.chair,
            None,
        ])

        response = self.ns["assign_chairman_of_record"](
            object(),
            self.event.id,
            chairman_user_id=self.chair.id,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("chairman=assigned", response.headers["location"])

        created = next(
            value
            for value in self.db.added
            if isinstance(value, EventChairAssignment)
        )

        self.assertEqual(created.event_id, self.event.id)
        self.assertEqual(created.chairman_user_id, self.chair.id)
        self.assertEqual(created.assigned_by_id, self.president.id)
        self.assertIsNone(created.acknowledged_at)
        self.assertIsNone(created.ended_at)

        log = self.slog.call_args.kwargs
        self.assertEqual(log["action"], "CHAIRMAN_OF_RECORD_ASSIGNED")
        self.assertEqual(log["event_id"], self.event.id)
        self.assertEqual(log["target_type"], "event_chair_assignment")
        self.assertEqual(log["details"]["chairman_user_id"], self.chair.id)
        self.assertIsNone(log["details"]["previous_chairman_user_id"])

        details = log["details"]

        self.assertNotIn(self.chair.handle, details.values())
        self.assertNotIn(self.president.handle, details.values())

        self.assertNotIn("chairman_handle", details)
        self.assertNotIn("assigned_by_handle", details)

    def test_reassignment_ends_old_record_and_creates_new_record(self):
        current = self.assignment(chairman=self.chair.id)

        self.actor = self.president
        self.db = FakeSession([
            self.event,
            self.chair2,
            current,
        ])

        response = self.ns["assign_chairman_of_record"](
            object(),
            self.event.id,
            chairman_user_id=self.chair2.id,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("chairman=changed", response.headers["location"])
        self.assertEqual(current.ended_at, self.now)

        created = [
            value
            for value in self.db.added
            if isinstance(value, EventChairAssignment) and value is not current
        ]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].chairman_user_id, self.chair2.id)
        self.assertIsNone(created[0].ended_at)

        log = self.slog.call_args.kwargs
        self.assertEqual(log["action"], "CHAIRMAN_OF_RECORD_CHANGED")
        self.assertEqual(
            log["details"]["previous_chairman_user_id"],
            self.chair.id,
        )

    def test_assigning_same_current_chair_is_noop(self):
        current = self.assignment(chairman=self.chair.id)

        self.actor = self.president
        self.db = FakeSession([
            self.event,
            self.chair,
            current,
        ])

        response = self.ns["assign_chairman_of_record"](
            object(),
            self.event.id,
            chairman_user_id=self.chair.id,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("chairman=unchanged", response.headers["location"])
        self.assertEqual(self.db.commit_count, 0)
        self.slog.assert_not_called()

    def test_ordinary_chair_cannot_assign_chairman_of_record(self):
        self.actor = self.chair
        self.db = FakeSession()

        with self.assertRaises(HTTPException) as exc:
            self.ns["assign_chairman_of_record"](
                object(),
                self.event.id,
                chairman_user_id=self.chair2.id,
            )

        self.assertEqual(exc.exception.status_code, 403)
        self.assertEqual(self.db.commit_count, 0)

    def test_assignee_must_hold_chairman_or_president_role(self):
        self.actor = self.president
        self.db = FakeSession([
            self.event,
            self.member,
        ])

        with self.assertRaises(HTTPException) as exc:
            self.ns["assign_chairman_of_record"](
                object(),
                self.event.id,
                chairman_user_id=self.member.id,
            )

        self.assertEqual(exc.exception.status_code, 400)
        self.assertEqual(self.db.commit_count, 0)

    def test_assigned_chair_can_acknowledge_once(self):
        current = self.assignment(chairman=self.chair.id)

        self.actor = self.chair
        self.db = FakeSession([current])

        response = self.ns["acknowledge_chairman_of_record"](
            object(),
            self.event.id,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(current.acknowledged_at, self.now)
        self.assertEqual(self.db.commit_count, 1)

        log = self.slog.call_args.kwargs
        self.assertEqual(
            log["action"],
            "CHAIRMAN_OF_RECORD_ACKNOWLEDGED",
        )
        self.assertEqual(
            log["details"],
            {"chairman_user_id": self.chair.id},
        )

    def test_other_user_cannot_acknowledge_assignment(self):
        current = self.assignment(chairman=self.chair.id)

        self.actor = self.member
        self.db = FakeSession([current])

        with self.assertRaises(HTTPException) as exc:
            self.ns["acknowledge_chairman_of_record"](
                object(),
                self.event.id,
            )

        self.assertEqual(exc.exception.status_code, 403)
        self.assertIsNone(current.acknowledged_at)
        self.assertEqual(self.db.commit_count, 0)
        self.slog.assert_not_called()

    def test_acknowledgement_is_idempotent(self):
        current = self.assignment(
            chairman=self.chair.id,
            acknowledged=self.now,
        )

        self.actor = self.chair
        self.db = FakeSession([current])

        response = self.ns["acknowledge_chairman_of_record"](
            object(),
            self.event.id,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.db.commit_count, 0)
        self.slog.assert_not_called()

    def test_event_menu_marks_current_chair_as_needing_acknowledgement(self):
        current = self.assignment(chairman=self.chair.id)

        self.actor = self.chair

        # event_menu query order:
        # 1. assignment history
        # 2. users referenced by history
        self.db = FakeSession([
            [current],
            [self.chair, self.president],
            [],  # attendance roster
        ])

        response = self.ns["event_menu"](object(), self.event.id)

        self.assertEqual(response["template_name"], "events_menu.html")
        context = response["context"]

        self.assertEqual(
            context["chairman_record"]["chairman_handle"],
            self.chair.handle,
        )
        self.assertEqual(
            context["chairman_record"]["assigned_by_handle"],
            self.president.handle,
        )
        self.assertTrue(context["needs_chair_acknowledgement"])
        self.assertEqual(len(context["chairman_history"]), 1)

    def test_event_menu_stops_requesting_ack_after_acknowledgement(self):
        current = self.assignment(
            chairman=self.chair.id,
            acknowledged=self.now,
        )

        self.actor = self.chair
        self.db = FakeSession([
            [current],
            [self.chair, self.president],
            [],  # attendance roster
        ])

        response = self.ns["event_menu"](object(), self.event.id)

        self.assertFalse(
            response["context"]["needs_chair_acknowledgement"]
        )
        self.assertEqual(
            response["context"]["chairman_record"]["acknowledged_at"],
            self.now,
        )

    def test_president_event_menu_gets_eligible_chair_choices_and_history(self):
        old = self.assignment(
            ident=90,
            chairman=self.chair.id,
            ended=self.now,
        )
        current = self.assignment(
            ident=91,
            chairman=self.chair2.id,
        )

        self.actor = self.president

        # query order:
        # 1. assignment history
        # 2. referenced users
        # 3. eligible candidate users
        self.db = FakeSession([
            [current, old],
            [self.chair, self.chair2, self.president],
            [self.chair, self.chair2, self.president, self.member],
            [],  # attendance roster
        ])

        response = self.ns["event_menu"](object(), self.event.id)
        context = response["context"]

        candidate_ids = {
            candidate["id"]
            for candidate in context["eligible_chairmen"]
        }

        self.assertIn(self.chair.id, candidate_ids)
        self.assertIn(self.chair2.id, candidate_ids)
        self.assertIn(self.president.id, candidate_ids)
        self.assertNotIn(self.member.id, candidate_ids)

        self.assertEqual(
            context["chairman_history"][0]["chairman_user_id"],
            self.chair2.id,
        )
        self.assertTrue(context["chairman_history"][0]["is_current"])
        self.assertFalse(context["chairman_history"][1]["is_current"])

    def test_template_has_assignment_attendance_and_unified_ack_modal(self):
        source = TEMPLATE.read_text()

        # Chairman-of-Record management remains present.
        self.assertIn('id="event-chairman-of-record"', source)
        self.assertIn(
            'action="/events/{{ event.id }}/chairman-of-record"',
            source,
        )

        # Attendance summary is available to authorized viewers.
        self.assertIn('id="event-attendance-summary"', source)
        self.assertIn("attendance_summary.observed", source)
        self.assertIn("attendance_summary.acknowledged", source)
        self.assertIn("attendance_summary.awaiting", source)

        # Entry acknowledgement is now one unified modal.
        self.assertIn(
            'id="event-entry-acknowledgement-modal"',
            source,
        )
        self.assertIn(
            'action="/events/{{ event.id }}/entry/acknowledge"',
            source,
        )
        self.assertIn(
            "{% set entry_ack_pending = "
            "attendance_ack_pending or chair_ack_pending %}",
            source,
        )
        self.assertIn(
            "eventEntryAcknowledgementPending",
            source,
        )

        # The old Chairman-only popup is no longer rendered.
        self.assertNotIn(
            'id="chair-acknowledgement-modal"',
            source,
        )


    def test_first_event_entry_creates_attendance(self):
        self.db = FakeSession([
            None,        # no existing attendance
            self.event,  # lock event
            None,        # re-check after event lock
        ])

        attendance, created = self.real_touch_event_attendance(
            self.db,
            self.event.id,
            self.member.id,
        )

        self.assertTrue(created)
        self.assertEqual(attendance.event_id, self.event.id)
        self.assertEqual(attendance.user_id, self.member.id)
        self.assertEqual(attendance.first_seen_at, self.now)
        self.assertEqual(attendance.last_seen_at, self.now)
        self.assertIsNone(attendance.acknowledged_at)
        self.assertEqual(self.db.commit_count, 1)
        self.assertEqual(self.db.refresh_count, 1)


    def test_repeat_event_entry_updates_last_seen_without_new_row(self):
        earlier = datetime(
            2026,
            9,
            17,
            0,
            45,
            tzinfo=timezone.utc,
        )

        attendance = self.attendance(
            first_seen=earlier,
            last_seen=earlier,
        )

        self.db = FakeSession([
            attendance,
        ])

        result, created = self.real_touch_event_attendance(
            self.db,
            self.event.id,
            self.member.id,
        )

        self.assertFalse(created)
        self.assertIs(result, attendance)
        self.assertEqual(attendance.first_seen_at, earlier)
        self.assertEqual(attendance.last_seen_at, self.now)
        self.assertEqual(self.db.commit_count, 1)


    def test_member_unified_acknowledgement_records_attendance_only(self):
        attendance = self.attendance(
            user_id=self.member.id,
        )

        self.actor = self.member
        self.db = FakeSession([
            attendance,
            None,  # no Chairman-of-Record assignment
        ])

        response = self.ns["acknowledge_event_entry"](
            object(),
            self.event.id,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn(
            "entry_acknowledged=1",
            response.headers["location"],
        )
        self.assertEqual(attendance.acknowledged_at, self.now)
        self.assertEqual(attendance.last_seen_at, self.now)

        self.assertEqual(self.slog.call_count, 1)
        log = self.slog.call_args.kwargs
        self.assertEqual(
            log["action"],
            "EVENT_ATTENDANCE_ACKNOWLEDGED",
        )
        self.assertEqual(
            log["details"],
            {"user_id": self.member.id},
        )


    def test_chair_unified_acknowledgement_records_attendance_and_role(self):
        attendance = self.attendance(
            user_id=self.chair.id,
        )
        current = self.assignment(
            chairman=self.chair.id,
        )

        self.actor = self.chair
        self.db = FakeSession([
            attendance,
            current,
        ])

        response = self.ns["acknowledge_event_entry"](
            object(),
            self.event.id,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(attendance.acknowledged_at, self.now)
        self.assertEqual(current.acknowledged_at, self.now)

        actions = [
            call.kwargs["action"]
            for call in self.slog.call_args_list
        ]

        self.assertEqual(
            actions,
            [
                "EVENT_ATTENDANCE_ACKNOWLEDGED",
                "CHAIRMAN_OF_RECORD_ACKNOWLEDGED",
            ],
        )


    def test_unified_acknowledgement_does_not_duplicate_semantic_logs(self):
        attendance = self.attendance(
            user_id=self.member.id,
            acknowledged=self.now,
        )

        self.actor = self.member
        self.db = FakeSession([
            attendance,
            None,
        ])

        response = self.ns["acknowledge_event_entry"](
            object(),
            self.event.id,
        )

        self.assertEqual(response.status_code, 303)

        # last_seen may still be refreshed, but acknowledgement itself
        # must not create another semantic event.
        self.assertEqual(self.db.commit_count, 1)
        self.slog.assert_not_called()


    def test_admin_event_menu_gets_attendance_summary_and_roster(self):
        attendance = self.attendance(
            user_id=self.member.id,
            acknowledged=self.now,
        )

        self.actor = self.admin

        # query order:
        # 1. Chairman assignment history
        # 2. eligible Chairman candidates
        # 3. event attendance rows
        # 4. users referenced by attendance rows
        self.db = FakeSession([
            [],
            [],
            [attendance],
            [self.member],
        ])

        response = self.ns["event_menu"](
            object(),
            self.event.id,
        )

        context = response["context"]

        self.assertTrue(context["can_view_attendance"])
        self.assertEqual(
            context["attendance_summary"],
            {
                "observed": 1,
                "acknowledged": 1,
                "awaiting": 0,
            },
        )
        self.assertEqual(
            context["attendance_roster"][0]["user_id"],
            self.member.id,
        )
        self.assertEqual(
            context["attendance_roster"][0]["handle"],
            self.member.handle,
        )


    def test_ordinary_member_cannot_view_attendance_roster(self):
        self.actor = self.member

        # Only Chairman assignment history is queried.
        self.db = FakeSession([
            [],
        ])

        response = self.ns["event_menu"](
            object(),
            self.event.id,
        )

        context = response["context"]

        self.assertFalse(context["can_view_attendance"])
        self.assertIsNone(context["attendance_summary"])
        self.assertEqual(context["attendance_roster"], [])


    def test_feature_does_not_change_role_permissions(self):
        source = MAIN.read_text()

        start = source.index(
            '@app.post("/events/{event_id}/chairman-of-record")'
        )
        end = source.index(
            '@app.get("/events/{event_id}/menu"',
            start,
        )
        feature_source = source[start:end]

        self.assertNotIn("grant_role(", feature_source)
        self.assertNotIn("revoke_role(", feature_source)

    def test_migration_enforces_one_active_assignment_per_event(self):
        source = MIGRATION.read_text()

        self.assertIn(
            'down_revision = "c2a94b6d810f"',
            source,
        )
        self.assertIn(
            '"uq_event_chair_assignment_active"',
            source,
        )
        self.assertIn(
            'postgresql_where=sa.text("ended_at IS NULL")',
            source,
        )


    def test_attendance_migration_is_unique_and_cascades(self):
        source = ATTENDANCE_MIGRATION.read_text()

        self.assertIn(
            'down_revision = "9d7f3b1a6c20"',
            source,
        )
        self.assertIn(
            '"uq_event_attendance_event_user"',
            source,
        )
        self.assertGreaterEqual(
            source.count('ondelete="CASCADE"'),
            2,
        )


if __name__ == "__main__":
    unittest.main()
