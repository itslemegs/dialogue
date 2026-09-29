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
            "effective_manual_closure",
            function_text,
        )
        self.assertIn(
            "or timer_closed",
            function_text,
        )
        self.assertIn(
            "continue",
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


    def test_record_detail_route_is_source_aware(self):
        text = (ROOT / "app/main.py").read_text()

        self.assertIn(
            '"/records/{source_key}/events/{event_id}"',
            text,
        )
        self.assertIn(
            "def record_event_detail",
            text,
        )

    def test_record_detail_rejects_open_events(self):
        text = (ROOT / "app/main.py").read_text()

        start = text.index("def record_event_detail")
        end = text.find("\n@app.", start)

        function_text = (
            text[start:end]
            if end != -1
            else text[start:]
        )

        self.assertIn(
            "effective_manual_closure",
            function_text,
        )
        self.assertIn(
            "or timer_closed",
            function_text,
        )
        self.assertIn(
            "raise HTTPException(status_code=404)",
            function_text,
        )

    def test_record_detail_uses_archive_session(self):
        text = (ROOT / "app/main.py").read_text()

        start = text.index("def record_event_detail")
        end = text.find("\n@app.", start)

        function_text = (
            text[start:end]
            if end != -1
            else text[start:]
        )

        self.assertIn(
            "get_record_session(source_key)",
            function_text,
        )

    def test_record_detail_template_has_no_forms(self):
        text = (
            ROOT
            / "app/templates/records/event_detail.html"
        ).read_text()

        self.assertNotIn("<form", text)
        self.assertNotIn("method=\"post\"", text.lower())


    def test_record_detail_materializes_archive_objects_before_session_close(self):
        text = (ROOT / "app/main.py").read_text()

        start = text.index("def record_event_detail")
        end = text.find("\n@app.", start)

        function_text = (
            text[start:end]
            if end != -1
            else text[start:]
        )

        self.assertIn(
            'event_record = {',
            function_text,
        )

        self.assertIn(
            'stage_records = [',
            function_text,
        )

        self.assertIn(
            'closure_user_handle = (',
            function_text,
        )

        self.assertIn(
            'event=event_record',
            function_text,
        )

        self.assertIn(
            'stages=stage_records',
            function_text,
        )

        self.assertNotIn(
            'closure_user.handle\n            if closure_user',
            function_text[
                function_text.find("return render("):
            ],
        )


    def test_record_detail_preserves_closing_revisions(self):
        main_text = (
            ROOT / "app/main.py"
        ).read_text()

        start = main_text.index(
            "def record_event_detail"
        )

        end = main_text.find(
            "\n@app.",
            start,
        )

        function_text = (
            main_text[start:end]
            if end != -1
            else main_text[start:]
        )

        self.assertIn(
            "ProposalFloorState",
            function_text,
        )

        self.assertIn(
            "closure_by_draft",
            function_text,
        )

        self.assertIn(
            "closure_by_amendment",
            function_text,
        )

        self.assertIn(
            '"closing_revision"',
            function_text,
        )

        template = (
            ROOT
            / "app/templates/records/event_detail.html"
        ).read_text()

        self.assertIn(
            "draft.closing_revision",
            template,
        )

        self.assertIn(
            "amendment.closing_revision",
            template,
        )


    def test_record_detail_preserves_discussion_history(self):
        main_text = (
            ROOT / "app/main.py"
        ).read_text()

        start = main_text.index(
            "def record_event_detail"
        )

        end = main_text.find(
            "\n@app.",
            start,
        )

        function_text = (
            main_text[start:end]
            if end != -1
            else main_text[start:]
        )

        for model_name in (
            "GeneralFloorLink",
            "Intervention",
            "ProposalRoom",
            "ProposalMessage",
            "ProposalIntervention",
        ):
            self.assertIn(
                model_name,
                function_text,
            )

        self.assertIn(
            "materialize_thread",
            function_text,
        )

        self.assertIn(
            "general_floor_records",
            function_text,
        )

        self.assertIn(
            "proposal_room_records",
            function_text,
        )

        self.assertIn(
            "proposal_floor_records",
            function_text,
        )

        template = (
            ROOT
            / "app/templates/records/event_detail.html"
        ).read_text()

        self.assertIn(
            "record_thread",
            template,
        )

        self.assertIn(
            "records.discussion_history",
            template,
        )

        self.assertIn(
            "general_floor_records",
            template,
        )

        self.assertIn(
            "proposal_room_records",
            template,
        )

        self.assertIn(
            "proposal_floor_records",
            template,
        )


    def test_record_detail_preserves_ballot_history(self):
        main_text = (
            ROOT / "app/main.py"
        ).read_text()

        start = main_text.index(
            "def record_event_detail"
        )
        end = main_text.find(
            "\n@app.",
            start,
        )

        function_text = (
            main_text[start:end]
            if end != -1
            else main_text[start:]
        )

        for model_name in (
            "ProposalEarlyBallot",
            "ProposalFormalBallot",
            "AmendmentVoteState",
            "AmendmentVote",
        ):
            self.assertIn(
                model_name,
                function_text,
            )

        self.assertIn(
            "materialize_ballots",
            function_text,
        )

        self.assertIn(
            "legacy_amendment_vote_summary",
            function_text,
        )

        template = (
            ROOT
            / "app/templates/records/event_detail.html"
        ).read_text()

        self.assertIn(
            "macro ballot_roster",
            template,
        )

        self.assertIn(
            "records.view_ballots",
            template,
        )

        self.assertIn(
            "records.legacy_amendment_vote",
            template,
        )


    def test_record_ballot_privacy_uses_chairman_of_record(self):
        main_text = (
            ROOT / "app/main.py"
        ).read_text()

        start = main_text.index(
            "def record_event_detail"
        )

        end = main_text.find(
            "\n@app.",
            start,
        )

        function_text = (
            main_text[start:end]
            if end != -1
            else main_text[start:]
        )

        self.assertIn(
            "is_record_chairman = bool(",
            function_text,
        )

        self.assertIn(
            'flags.get("IS_ADMIN")',
            function_text,
        )

        self.assertIn(
            'flags.get("IS_PRESIDENT")',
            function_text,
        )

        self.assertIn(
            '"chairman_user_id"',
            function_text,
        )

        self.assertIn(
            "can_view_event_record = bool(",
            function_text,
        )

        self.assertIn(
            "can_view_individual_ballots = bool(",
            function_text,
        )

        template = (
            ROOT
            / "app/templates/records/event_detail.html"
        ).read_text()

        self.assertIn(
            "macro ballot_roster(ballots, can_view=False)",
            template,
        )

        self.assertIn(
            "ballots and can_view",
            template,
        )

        self.assertIn(
            "can_view_individual_ballots",
            template,
        )


    def test_record_permissions_run_after_final_chairman_is_built(self):
        main_text = (
            ROOT / "app/main.py"
        ).read_text()

        start = main_text.index(
            "def record_event_detail"
        )

        end = main_text.find(
            "\n@app.",
            start,
        )

        function_text = (
            main_text[start:end]
            if end != -1
            else main_text[start:]
        )

        chairman_assignment = function_text.index(
            "final_chairman ="
        )

        permission_check = function_text.index(
            "is_record_chairman = bool("
        )

        self.assertLess(
            chairman_assignment,
            permission_check,
        )


    def test_records_index_is_available_to_authenticated_members(self):
        main_text = (
            ROOT / "app/main.py"
        ).read_text()

        start = main_text.index(
            "def records_index"
        )

        end = main_text.find(
            "\n@app.",
            start,
        )

        function_text = (
            main_text[start:end]
            if end != -1
            else main_text[start:]
        )

        # Authentication remains required.
        self.assertIn(
            "if user is None:",
            function_text,
        )

        # All authenticated users may browse closed Records.
        self.assertNotIn(
            "chaired_record_keys = set()",
            function_text,
        )

        self.assertNotIn(
            "if not can_view_all_records",
            function_text,
        )

        self.assertIn(
            "record_events=record_events",
            function_text,
        )

    def test_record_detail_resolves_chairman_by_source_handle(self):
        main_text = (
            ROOT / "app/main.py"
        ).read_text()

        start = main_text.index(
            "def record_event_detail"
        )

        end = main_text.find(
            "\n@app.",
            start,
        )

        function_text = (
            main_text[start:end]
            if end != -1
            else main_text[start:]
        )

        self.assertIn(
            "source_viewer_id",
            function_text,
        )

        self.assertIn(
            "User.handle == user.handle",
            function_text,
        )


    def test_normal_record_view_hides_person_level_admin_data(self):
        main_text = (
            ROOT / "app/main.py"
        ).read_text()

        start = main_text.index(
            "def record_event_detail"
        )

        end = main_text.find(
            "\n@app.",
            start,
        )

        function_text = (
            main_text[start:end]
            if end != -1
            else main_text[start:]
        )

        self.assertIn(
            "can_view_event_record = bool(user)",
            function_text,
        )

        self.assertIn(
            "can_view_private_record = bool(",
            function_text,
        )

        self.assertIn(
            "can_view_individual_ballots = bool(",
            function_text,
        )

        self.assertIn(
            "can_view_session_log = bool(",
            function_text,
        )

        self.assertIn(
            'source_key == "a"',
            function_text,
        )

        template = (
            ROOT
            / "app/templates/records/event_detail.html"
        ).read_text()

        self.assertIn(
            "can_view_private_record and chairman_history",
            template,
        )

        self.assertIn(
            "can_view_private_record and attendance_roster",
            template,
        )

        self.assertIn(
            "can_view_session_log",
            template,
        )

        self.assertIn(
            'href="{{ session_log_href }}"',
            template,
        )
        self.assertIn(
            "{% if can_view_session_log %}",
            template,
        )


    def test_session_log_backend_includes_president_access(self):
        route_text = (
            ROOT
            / "app/routes/session_log.py"
        ).read_text()

        self.assertIn(
            'flags.get("IS_PRESIDENT")',
            route_text,
        )

        self.assertIn(
            '@router.get("/events/{event_id}/session-log"',
            route_text,
        )


if __name__ == "__main__":
    unittest.main()
