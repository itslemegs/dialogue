"""Regression checks for the Dashboard -> Records event lifecycle."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EventRecordsLifecycleTests(unittest.TestCase):

    def test_dashboard_records_link_points_to_records_index(self):
        text = (ROOT / "app/templates/dashboard.html").read_text()

        self.assertIn('href="/records"', text)
        self.assertNotIn('href="/records/menu"', text)

    def test_manual_close_route_exists(self):
        text = (ROOT / "app/main.py").read_text()

        self.assertIn(
            '@app.post("/events/{event_id}/close")',
            text,
        )
        self.assertIn(
            "def close_event_to_records",
            text,
        )

    def test_manual_close_preserves_scheduled_end_time(self):
        """
        Manual closure must use EventArchiveState rather than rewriting
        Event.ends_at, so the historical schedule remains intact.
        """
        text = (ROOT / "app/main.py").read_text()

        start = text.index("def close_event_to_records")
        end = text.find("\n@app.", start)

        function_text = (
            text[start:end]
            if end != -1
            else text[start:]
        )

        self.assertIn(
            "EventArchiveState(",
            function_text,
        )

        self.assertNotIn(
            "event.ends_at =",
            function_text,
        )

    def test_dashboard_excludes_manual_archive_state(self):
        text = (ROOT / "app/main.py").read_text()

        start = text.index("def _dashboard_event_cards")
        end = text.index(
            '@app.get("/dashboard"',
            start,
        )

        function_text = text[start:end]

        self.assertIn(
            "EventArchiveState.event_id",
            function_text,
        )
        self.assertIn(
            "manually_closed_event_ids",
            function_text,
        )

    def test_records_only_accept_closed_events(self):
        text = (ROOT / "app/main.py").read_text()

        start = text.index("def records_index")
        end = text.find("\n@app.", start)

        function_text = (
            text[start:end]
            if end != -1
            else text[start:]
        )

        self.assertIn(
            "timer_closed",
            function_text,
        )
        self.assertIn(
            "manual_closure",
            function_text,
        )
        self.assertIn(
            "if not (manual_closure or timer_closed):",
            function_text,
        )

    def test_archive_database_is_not_given_manual_closure_table(self):
        """
        Historical Instance B remains on its original schema.
        Manual EventArchiveState is queried only for source A.
        """
        text = (ROOT / "app/main.py").read_text()

        start = text.index("def records_index")
        end = text.find("\n@app.", start)

        function_text = (
            text[start:end]
            if end != -1
            else text[start:]
        )

        self.assertIn(
            'if source_key == "a":',
            function_text,
        )


if __name__ == "__main__":
    unittest.main()
