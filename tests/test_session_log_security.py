import asyncio
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.routes import session_log


class FakeDB:
    def __init__(self):
        self.committed = False

    def commit(self):
        self.committed = True


class FakeSessionContext:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyRequest:
    def __init__(self, payload):
        self._payload = payload
        self.cookies = {}
        self.state = SimpleNamespace()
        self.headers = {}
        self.client = None
        self.url = SimpleNamespace(path="/api/session-log/client")
        self.method = "POST"

    async def json(self):
        return self._payload


class SessionLogSecurityTests(unittest.TestCase):
    def test_event_user_reuses_existing_event_access_helper(self):
        user = SimpleNamespace(id=12)
        db = object()
        seen = {}

        fake_main = types.ModuleType("app.main")

        def require_event_access(*, db, user, event_id):
            seen["db"] = db
            seen["user"] = user
            seen["event_id"] = event_id
            return SimpleNamespace(id=event_id)

        fake_main._require_event_access = require_event_access

        with (
            patch.object(session_log, "_require_user", return_value=user),
            patch.dict(sys.modules, {"app.main": fake_main}),
        ):
            result = session_log._require_event_user(
                request=object(),
                event_id=15,
                db=db,
            )

        self.assertIs(result, user)
        self.assertIs(seen["db"], db)
        self.assertIs(seen["user"], user)
        self.assertEqual(seen["event_id"], 15)

    def test_ordinary_member_cannot_view_experiment_logs(self):
        user = SimpleNamespace(id=18)

        with (
            patch.object(
                session_log,
                "_require_event_user",
                return_value=user,
            ),
            patch.object(
                session_log,
                "effective_flags",
                return_value={
                    "IS_MEMBER": True,
                    "IS_ADMIN": False,
                    "IS_PRESIDENT": False,
                    "IS_CHAIR": False,
                },
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                session_log._require_log_viewer(
                    request=object(),
                    event_id=15,
                    db=object(),
                )

        self.assertEqual(ctx.exception.status_code, 403)

    def test_admin_president_and_chair_can_view_logs(self):
        user = SimpleNamespace(id=2)

        for role in ("IS_ADMIN", "IS_PRESIDENT", "IS_CHAIR"):
            with self.subTest(role=role):
                flags = {
                    "IS_MEMBER": True,
                    "IS_ADMIN": False,
                    "IS_PRESIDENT": False,
                    "IS_CHAIR": False,
                }
                flags[role] = True

                with (
                    patch.object(
                        session_log,
                        "_require_event_user",
                        return_value=user,
                    ),
                    patch.object(
                        session_log,
                        "effective_flags",
                        return_value=flags,
                    ),
                ):
                    result = session_log._require_log_viewer(
                        request=object(),
                        event_id=15,
                        db=object(),
                    )

                self.assertIs(result, user)

    def test_event_telemetry_uses_authenticated_user_not_payload_user(self):
        db = FakeDB()
        request = DummyRequest(
            {
                "event_id": 15,
                "user_id": 999999,
                "action": "TEST_EVENT",
                "details": {},
            }
        )

        captured = {}

        def capture_log(db_arg, **kwargs):
            captured.update(kwargs)

        with (
            patch.object(
                session_log,
                "get_session",
                return_value=FakeSessionContext(db),
            ),
            patch.object(
                session_log,
                "_user_id_from_session_cookie",
                return_value=7,
            ),
            patch.object(
                session_log,
                "_require_event_user",
                return_value=SimpleNamespace(id=42),
            ) as require_event,
            patch.object(
                session_log,
                "add_session_log",
                side_effect=capture_log,
            ),
        ):
            result = asyncio.run(
                session_log.client_session_log(request)
            )

        self.assertEqual(result, {"ok": True})
        require_event.assert_called_once()
        self.assertEqual(captured["event_id"], 15)
        self.assertEqual(captured["user_id"], 42)
        self.assertNotEqual(captured["user_id"], 999999)
        self.assertTrue(db.committed)

    def test_unauthorized_event_telemetry_is_rejected(self):
        db = FakeDB()
        request = DummyRequest(
            {
                "event_id": 15,
                "user_id": 999999,
                "action": "TEST_EVENT",
            }
        )

        with (
            patch.object(
                session_log,
                "get_session",
                return_value=FakeSessionContext(db),
            ),
            patch.object(
                session_log,
                "_user_id_from_session_cookie",
                return_value=7,
            ),
            patch.object(
                session_log,
                "_require_event_user",
                side_effect=HTTPException(
                    status_code=403,
                    detail="Event access denied",
                ),
            ),
            patch.object(session_log, "add_session_log") as add_log,
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    session_log.client_session_log(request)
                )

        self.assertEqual(ctx.exception.status_code, 403)
        add_log.assert_not_called()
        self.assertFalse(db.committed)

    def test_non_event_client_logging_keeps_existing_behavior(self):
        db = FakeDB()
        request = DummyRequest(
            {
                "user_id": 55,
                "action": "PAGE_VIEW",
                "details": {},
            }
        )

        captured = {}

        def capture_log(db_arg, **kwargs):
            captured.update(kwargs)

        event_guard = Mock()

        with (
            patch.object(
                session_log,
                "get_session",
                return_value=FakeSessionContext(db),
            ),
            patch.object(
                session_log,
                "_user_id_from_session_cookie",
                return_value=None,
            ),
            patch.object(
                session_log,
                "_require_event_user",
                event_guard,
            ),
            patch.object(
                session_log,
                "add_session_log",
                side_effect=capture_log,
            ),
        ):
            result = asyncio.run(
                session_log.client_session_log(request)
            )

        self.assertEqual(result, {"ok": True})
        event_guard.assert_not_called()
        self.assertIsNone(captured["event_id"])
        self.assertEqual(captured["user_id"], 55)
        self.assertTrue(db.committed)


if __name__ == "__main__":
    unittest.main()
