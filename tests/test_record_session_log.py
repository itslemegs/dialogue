import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RecordSessionLogTests(unittest.TestCase):

    def test_record_log_routes_are_source_aware(self):
        text = (
            ROOT / "app/routes/session_log.py"
        ).read_text()

        self.assertIn(
            '"/records/{source_key}/events/"',
            text,
        )

        self.assertIn(
            '"{event_id}/session-log"',
            text,
        )

        self.assertIn(
            '"{event_id}/session-log/export"',
            text,
        )

        self.assertIn(
            "get_record_session(source_key)",
            text,
        )

    def test_record_logs_are_president_only(self):
        text = (
            ROOT / "app/routes/session_log.py"
        ).read_text()

        start = text.index(
            "def _require_record_log_viewer"
        )

        end = text.find(
            "\ndef ",
            start + 10,
        )

        block = text[
            start:end if end != -1 else len(text)
        ]

        self.assertIn(
            'flags.get("IS_PRESIDENT")',
            block,
        )

        self.assertNotIn(
            'flags.get("IS_ADMIN")',
            block,
        )

        self.assertNotIn(
            'flags.get("IS_CHAIR")',
            block,
        )

    def test_source_b_closure_uses_live_override(self):
        text = (
            ROOT / "app/routes/session_log.py"
        ).read_text()

        self.assertIn(
            "RecordSourceArchiveState",
            text,
        )

        self.assertIn(
            'RecordSourceArchiveState.source_key',
            text,
        )

        self.assertIn(
            '== "b"',
            text,
        )

    def test_archive_session_is_never_written(self):
        text = (
            ROOT / "app/routes/session_log.py"
        ).read_text()

        self.assertNotIn(
            "record_db.commit()",
            text,
        )

        self.assertNotIn(
            "db.commit()",
            text[
                text.index(
                    "def export_record_session_log"
                ):
                text.index(
                    "def _render_log_view"
                )
            ],
        )

    def test_record_detail_uses_source_aware_log_link(self):
        main_text = (
            ROOT / "app/main.py"
        ).read_text()

        self.assertIn(
            "session_log_href = (",
            main_text,
        )

        self.assertIn(
            'f"/records/{source_key}/events/"',
            main_text,
        )

        template_text = (
            ROOT
            / "app/templates/records/event_detail.html"
        ).read_text()

        self.assertIn(
            'href="{{ session_log_href }}"',
            template_text,
        )

    def test_session_log_template_uses_dynamic_urls(self):
        text = (
            ROOT / "app/templates/session_log.html"
        ).read_text()

        self.assertIn(
            'href="{{ back_href }}"',
            text,
        )

        self.assertIn(
            '{{ export_href_base }}?format=csv',
            text,
        )

        self.assertIn(
            '{{ export_href_base }}?format=json',
            text,
        )


if __name__ == "__main__":
    unittest.main()
