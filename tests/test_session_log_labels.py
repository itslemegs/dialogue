"""Logging metadata regressions: fake sessions only, no application startup."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from starlette.requests import Request
from app.services import session_log as logs

ROOT = Path(__file__).resolve().parents[1]


class SessionLogLabelTests(unittest.TestCase):
    def request(self, path):
        request = Request({'type': 'http', 'method': 'POST', 'path': path,
                           'headers': [], 'query_string': b'',
                           'route': SimpleNamespace(path='/events/{event_id}/route')})
        request.state.exp_session_key = 'illustrative-session'
        request.state.request_id = 'illustrative-request'
        return request

    def test_page_context_for_participant_workflows(self):
        cases = [
            ('/dashboard', 'dashboard', 'dashboard', {}),
            ('/events/1/menu', 'event_menu', 'event_menu', {'event_id': 1}),
            ('/events/1/propose-agenda', 'agenda_setting', 'propose_agenda', {}),
            ('/events/1/review-agenda/2/decision', 'agenda_review', 'review_agenda', {'proposal_id': 2}),
            ('/events/1/view-agenda', 'agenda_setting', 'view_agenda', {}),
            ('/events/1/general-floor', 'general_floor', 'general_floor_index', {}),
            ('/events/1/general-floor/2/interventions', 'general_floor', 'general_floor_item', {'proposal_id': 2}),
            ('/events/1/proposal-discussion', 'proposal_discussion', 'proposal_discussion_index', {}),
            ('/events/1/proposal-discussion/2', 'proposal_discussion', 'rooms_index', {'proposal_id': 2}),
            ('/events/1/proposal-discussion/2/rooms/3/draft/save', 'proposal_discussion', 'proposal_room', {'room_id': 3}),
            ('/events/1/proposal-discussion/2/rooms/3/drafts/4/amendments/5', 'proposal_discussion', 'amendment_detail', {'proposal_id': 2, 'room_id': 3, 'draft_id': 4, 'amendment_id': 5}),
            ('/events/1/proposal-floor/amendment/5/formal/vote', 'proposal_floor', 'proposal_floor_item', {'amendment_id': 5}),
            ('/events/1/proposal-floor/draft/4', 'proposal_floor', 'proposal_floor_item', {'draft_id': 4}),
            ('/events/1/decision', 'decision', 'decision', {}),
            ('/drafts/4/cosign', 'proposal_discussion', 'draft_detail', {'draft_id': 4}),
        ]
        for path, phase, page_kind, ids in cases:
            with self.subTest(path=path):
                context = logs.page_log_context(path)
                self.assertEqual(context['phase'], phase)
                self.assertEqual(context['page_kind'], page_kind)
                for key, value in ids.items():
                    self.assertEqual(context[key], value)
        self.assertEqual(logs.page_log_context('/unknown?private=secret'), {})

    def test_semantic_target_identity_and_details_remain_authoritative(self):
        db = Mock()
        request = self.request('/events/1/general-floor/2/interventions')
        row = logs.add_session_log(db, request=request, user_id=7,
            action='GENERAL_INTERVENTION_POSTED', phase='general_floor',
            target_type='intervention', target_id=20,
            details={'question_id': 3, 'body_chars': 40})
        self.assertEqual((row.event_id, row.user_id, row.target_type, row.target_id), (1, 7, 'intervention', '20'))
        self.assertEqual((row.session_key, row.request_id), ('illustrative-session', 'illustrative-request'))
        self.assertEqual(row.route, '/events/{event_id}/route')
        self.assertEqual(json.loads(row.details_json), {'page_kind':'general_floor_item', 'proposal_id':2, 'question_id':3, 'body_chars':40})
        db.add.assert_called_once_with(row)
        db.commit.assert_not_called()

    def test_client_phase_is_promoted_without_inferring_event_authorization(self):
        row = logs.add_session_log(Mock(), request=self.request('/api/session-log/client'),
            source='client', page='/events/1/general-floor/2', action='ROR_INVITE_REQUESTED',
            details={'phase': 'general_floor', 'page_kind': 'general_floor_item'})
        self.assertEqual(row.phase, 'general_floor')
        self.assertIsNone(row.event_id)  # Endpoint access checks own event identity.
        self.assertEqual((row.target_type, row.target_id), ('agenda_proposal', '2'))
        self.assertIsNone(row.route)

    def test_resolved_draft_event_is_exportable_without_database_lookup(self):
        request = self.request('/drafts/4/cosign')
        request.state.session_log_context = {'event_id':1, 'proposal_id':2, 'room_id':3, 'draft_id':4}
        row = logs.add_session_log(Mock(), request=request, source='server', action='HTTP_REQUEST', status_code=303)
        self.assertEqual(row.event_id, 1)
        self.assertEqual(json.loads(row.details_json), {'page_kind':'draft_detail', 'draft_id':4, 'proposal_id':2, 'room_id':3, 'target_inferred_from':'route'})
        self.assertEqual((row.action,row.status_code), ('HTTP_REQUEST',303))
        # Execute the actual metadata assignment against an already-loaded object.
        tree = ast.parse((ROOT/'app/main.py').read_text())
        for name in ('draft_detail','cosign_draft','reintroduce_draft'):
            fn = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            assignment = next(n for n in ast.walk(fn) if isinstance(n,ast.Assign)
                              and ast.unparse(n.targets[0])=='request.state.session_log_context')
            req = self.request('/drafts/4')
            exec(compile(ast.Module(body=[assignment],type_ignores=[]),'metadata','exec'),
                 {'request':req,'draft_id':4,'d':SimpleNamespace(id=4,event_id=1,proposal_id=2,room_id=3)})
            self.assertEqual(req.state.session_log_context, request.state.session_log_context)

    def test_browser_labels_and_submitter_override(self):
        if not shutil.which('node'):
            self.skipTest('Node required for client logging test')
        result = subprocess.run(['node','tests/session_log_labels.js'], cwd=ROOT,
                                capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        source = (ROOT/'app/templates/rooms/show.html').read_text()
        self.assertIn('data-log-submit-action="SUBMIT_DRAFT_AS_L_DOC"',source)
