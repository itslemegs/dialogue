"""Read-only log viewer tests; no live database or application startup."""
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates
from app import i18n
from app.models import ExperimentSessionLog
from app.routes import session_log
from app.services.session_log_view import activity_for, build_log_view
from tests.test_i18n import isolated_functions, request

ROOT = Path(__file__).resolve().parents[1]


class SessionLogViewTests(unittest.TestCase):
    def setUp(self):
        self.templates = Jinja2Templates(directory=str(ROOT/'app/templates'))
        i18n.install_jinja(self.templates.env)
        self.templates.env.filters['jst'] = isolated_functions()['_format_jst']

    def record(self, id=1, **changes):
        values = dict(id=id, created_at=datetime(2027,5,20,9,46,id,tzinfo=timezone.utc),
            event_id=7, user_id=None, session_key='example-session', source='semantic',
            action='GENERAL_INTERVENTION_POSTED', phase='general_floor',
            page='/events/7/general-floor/8', target_type='intervention',target_id='17',
            details_json='{"proposal_id":8}')
        values.update(changes)
        return ExperimentSessionLog(**values)

    def render(self, logs, locale='en'):
        with patch.dict(sys.modules, {'app.main':SimpleNamespace(templates=self.templates)}):
            return session_log._render_log_view(logs, {}, 7, request(locale))

    def test_jst_display_keeps_raw_timestamp_and_all_details(self):
        for timestamp in [datetime(2027,5,20,9,46,1), datetime(2027,5,20,9,46,1,tzinfo=timezone.utc)]:
            log = self.record(created_at=timestamp, details_json=json.dumps({'note':'x'*650,'status':'TABLED'}))
            for locale in ('en','ja'):
                html = self.render([log], locale)
                self.assertIn('2027-05-20 18:46:01 JST',html)
                self.assertIn('Asia/Tokyo',html)
                self.assertIn(timestamp.isoformat(),html)
                self.assertIn('x'*650,html)
                self.assertIn('TABLED',html)
                self.assertEqual(log.created_at,timestamp)
                self.assertIn('<details',html)
        malformed = self.render([self.record(details_json='{old malformed data')])
        self.assertIn('{old malformed data',malformed)

    def test_grouping_phase_precedence_and_historical_fallbacks(self):
        cases = [
            ({'phase':'agenda_review'}, {}, 'agenda'),
            ({'phase':'general_floor','target_type':'amendment'}, {}, 'general_floor'),
            ({'phase':'proposal_floor','target_type':'amendment'}, {}, 'proposal_floor'),
            ({'phase':'proposal_discussion','target_type':'proposal_room'}, {}, 'proposal_discussion'),
            ({'phase':'proposal_discussion','target_type':'proposal_draft'}, {}, 'drafting'),
            ({'phase':'proposal_discussion','action':'AMENDMENT_SUBMITTED'}, {}, 'amendment'),
            ({'phase':'decision'}, {}, 'decision'),
            ({'page':'/events/7/general-floor/8'}, {}, 'general_floor'),
            ({'action':'FORMAL_VOTE_CAST'}, {}, 'proposal_floor'),
            ({}, {'phase':'proposal_discussion','page_kind':'amendment_detail'}, 'amendment'),
            ({'phase':'unknown','action':'DRAFT_SAVED'}, {}, 'other'),
        ]
        for record, details, expected in cases:
            with self.subTest(record=record):
                self.assertEqual(activity_for(record,details),expected)

    def test_newest_first_query_and_stable_group_order(self):
        logs = [self.record(3),self.record(2,phase='decision'),self.record(1)]
        db=MagicMock(); db.__enter__.return_value=db
        db.exec.return_value.all.return_value=logs
        with patch.object(session_log,'get_session',return_value=db), \
             patch.object(session_log,'_require_log_viewer'), \
             patch.dict(sys.modules,{'app.main':SimpleNamespace(templates=self.templates)}):
            html=session_log.view_session_log(7,request('en'))
        query=str(db.exec.call_args.args[0])
        self.assertIn('created_at DESC',query)
        self.assertIn('id DESC',query)
        self.assertEqual(db.exec.call_args.args[0].compile().params['param_1'],300)
        self.assertLess(html.index('18:46:03 JST'),html.index('18:46:01 JST'))
        self.assertLess(html.index('data-log-group="general_floor"'),html.index('data-log-group="decision"'))
        db.commit.assert_not_called(); db.add.assert_not_called()

    def test_sources_actions_and_untrusted_strings_are_safe(self):
        logs=[self.record(1,source='client',action='SUBMIT_DRAFT_AS_L_DOC'),
              self.record(2,source='server',action='HTTP_REQUEST'),self.record(3)]
        html=self.render(logs)
        for label in ('Interaction','Confirmed action','Request','Draft submission attempted',
                      'SUBMIT_DRAFT_AS_L_DOC','GENERAL_INTERVENTION_POSTED'):
            self.assertIn(label,html)
        self.assertEqual(logs[0].action,'SUBMIT_DRAFT_AS_L_DOC')
        attack='<script>alert(1)</script>'
        html=self.render([self.record(action=attack,page=attack,details_json=attack)])
        self.assertNotIn(attack,html)
        self.assertIn('&lt;script&gt;',html)
        for locale in ('en','ja'):
            self.templates.env.parse((ROOT/'app/templates/session_log.html').read_text())
            self.assertIn(f'<html lang="{locale}">',self.render(logs,locale))

    def test_export_timestamp_columns_and_permission_are_unchanged(self):
        log=self.record()
        db=MagicMock();db.__enter__.return_value=db
        db.exec.return_value.all.return_value=[log]
        with patch.object(session_log,'get_session',return_value=db), \
             patch.object(session_log,'_require_log_viewer') as guard:
            response=session_log.export_session_log(7,request('en'))
        guard.assert_called_once()
        rows=list(csv.DictReader(io.StringIO(response.body.decode())))
        self.assertEqual(rows[0]['created_at'],log.created_at.isoformat())
        self.assertEqual(rows[0]['action'],log.action)
        self.assertEqual(json.loads(rows[0]['details']),{'proposal_id':8})
        db.commit.assert_not_called()
        for handler in (session_log.view_session_log,session_log.export_session_log):
            db.reset_mock()
            with patch.object(session_log,'get_session',return_value=db), \
                 patch.object(session_log,'_require_log_viewer',side_effect=HTTPException(403)):
                with self.assertRaises(HTTPException) as raised:
                    handler(7,request('en'))
            self.assertEqual(raised.exception.status_code,403)
            db.exec.assert_not_called()

    def test_filters_execute_without_replacing_rows(self):
        if not shutil.which('node'):
            self.skipTest('Node required for filter test')
        result=subprocess.run(['node','tests/session_log_view.js'],cwd=ROOT,
                              text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
