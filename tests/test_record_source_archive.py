import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RecordSourceArchiveTests(unittest.TestCase):

    def test_source_archive_model_is_source_aware(self):
        text = (ROOT / "app/models.py").read_text()

        self.assertIn(
            "class RecordSourceArchiveState",
            text,
        )
        self.assertIn(
            '"record_source_archive_state"',
            text,
        )
        self.assertIn(
            '"source_key",',
            text,
        )
        self.assertIn(
            '"event_id",',
            text,
        )

    def test_instance_b_close_is_president_only(self):
        text = (ROOT / "app/main.py").read_text()

        marker = (
            '@app.post("/records/b/events/'
            '{event_id}/close")'
        )

        start = text.index(marker)
        end = text.find("\n@app.", start + 10)

        route = text[
            start:end if end != -1 else len(text)
        ]

        self.assertIn(
            'flags.get("IS_PRESIDENT")',
            route,
        )
        self.assertIn(
            'get_record_session("b")',
            route,
        )
        self.assertIn(
            'RecordSourceArchiveState(',
            route,
        )

        # Historical B itself must never be committed to.
        self.assertNotIn(
            "archive_db.commit()",
            route,
        )

    def test_records_index_uses_source_override(self):
        text = (ROOT / "app/main.py").read_text()

        start = text.index("def records_index")
        end = text.find("\n@app.", start)

        block = text[
            start:end if end != -1 else len(text)
        ]

        self.assertIn(
            "source_overrides",
            block,
        )
        self.assertIn(
            "effective_manual_closure",
            block,
        )
        self.assertIn(
            "record_archive_candidates",
            block,
        )

    def test_record_detail_accepts_source_override(self):
        text = (ROOT / "app/main.py").read_text()

        start = text.index(
            "def record_event_detail"
        )

        block = text[start:]

        self.assertIn(
            "source_override",
            block,
        )
        self.assertIn(
            "effective_manual_closure",
            block,
        )
        self.assertIn(
            'source_override["closed_by_handle"]',
            block,
        )


if __name__ == "__main__":
    unittest.main()
