"""Closure handlers and archived pages, using isolated in-memory route fixtures."""
import ast
import asyncio
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import Mock, patch

from fastapi import Form, HTTPException, Request
from fastapi.responses import RedirectResponse
import test_deliberation_polling as polling

ROOT = Path(__file__).resolve().parents[1]


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.f = polling.PollingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.initial_floor_page(proposal=True)
        self.ns = self.f.ns
        self.ns.update(Form=Form, RedirectResponse=RedirectResponse, slog=Mock(),
                       ProposalDraftStatus=N(ADOPTED='ADOPTED',WITHDRAWN='WITHDRAWN',TABLED='TABLED',REINTRODUCED='REINTRODUCED'))
        locking = patch.object(polling.Query, 'with_for_update', lambda s:s, create=True)
        locking.start(); self.addCleanup(locking.stop)
        names = {'PFloorTarget','_resolve_pfloor_target','_pfloor_target_check','_require_open_pfloor',
            '_require_open_pfloor_scope','_pfloor_get_or_create_state','pfloor_close_discussion',
            'pfloor_close_confirmation','proposal_floor_post_intervention','pfloor_withdraw','pfloor_register',
            'pfloor_call_next','pfloor_finish_current','pfloor_take_now','pfloor_invite_sponsors','pfloor_invite_ror',
            'pfloor_early_open','pfloor_early_close','pfloor_early_vote','pfloor_formal_open','pfloor_formal_close',
            'pfloor_formal_vote','amendment_vote','amendment_vote_open','amendment_vote_close',
            '_pfloor_enqueue_from_invite','pfloor_toggle','decision_page','_decision_compute_outcome_draft','proposal_floor_index'}
        nodes=[]
        for n in ast.parse((ROOT/'app/main.py').read_text()).body:
            if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names:
                n.decorator_list=[];nodes.append(n)
        module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module,'archive_handlers','exec'),self.ns)
        for st in [self.f.initial_proposal,self.f.initial_amendment]:
            st.closing_revision_text=st.closing_revision_by_id=st.closing_revision_at=None
        self.ns['_get_or_create_amend_vote_state']=lambda db,ident:N(is_open=True)

    def request(self, chair=True, locale='en', form=None):
        r=Request({'type':'http','method':'POST','path':'/events/10/proposal-floor/draft/40',
            'headers':[(b'cookie',b'session=3' if chair else b'session=2'),(b'x-ui-language',locale.encode())]})
        r.state.help_ctx={}
        if form is not None:
            async def parsed():return form
            r.form=parsed
        return r

    def writable(self):
        # Only these explicit test-local doubles permit persistence.
        self.f.db.add=Mock()
        self.f.db.commit=Mock()
        self.f.db.refresh=Mock()

    def close(self,kind='draft',text='',chair=True):
        return self.ns['pfloor_close_discussion'](10,kind,50 if kind=='amendment' else 40,
                                                 self.request(chair),closing_revision=text)

    def test_closed_gets_history_revision_and_permissions_in_both_languages(self):
        for amendment in [False,True]:
            st=self.f.initial_amendment if amendment else self.f.initial_proposal
            st.is_open=False;st.closing_revision_text='Final <script>authored</script> 日本語'
            st.closing_revision_at=self.f.now;st.closing_revision_by_id=3
            self.f.add('ProposalIntervention',id=2 if amendment else 1,event_id=10,proposal_id=20,
                draft_id=None if amendment else 40,amendment_id=50 if amendment else None,
                by_user=1,body='Historical content 日本語',parent_id=None,local_no=1,created_at=self.f.now)
            before=copy.deepcopy([r.__dict__ for r in self.f.rows])
            for locale in ['en','ja']:
                for role in ['member','chair']:
                    response,data,base=self.f.initial_floor_page(True,amendment,locale,role)
                    html=response.body.decode()
                    self.assertEqual(response.status_code,200)
                    self.assertIn('Historical content 日本語',html)
                    self.assertIn('Final &lt;script&gt;authored&lt;/script&gt; 日本語',html)
                    self.assertIn('討議は終了しました' if locale=='ja' else 'Discussion Closed',html)
                    import re
                    self.assertNotRegex(html,r'<form\b[^>]*method="post"[^>]*action="/events/10/proposal-floor')
                    self.assertTrue(data['read_only']);self.assertFalse(data['can_speak'])
                    _,ids=self.f.floor_dom(html)
                    self.assertEqual(ids['pf-voting']['text'],self.f.floor_dom('<div id="vote">'+data['voting_html']+'</div>')[1]['vote']['text'])
                    self.assertNotIn('/formal/vote',data['voting_html'])
            self.assertEqual(before,[r.__dict__ for r in self.f.rows])

    def test_chair_closes_with_or_without_revision_preserving_originals_and_logs(self):
        for kind in ['draft','amendment']:
            st=self.f.initial_amendment if kind=='amendment' else self.f.initial_proposal
            for text in ['', 'Negotiated <b>wording</b> 日本語\nline two']:
                st.is_open=True;st.closing_revision_text=None
                original=copy.deepcopy((self.f.draft.__dict__,self.f.amend.__dict__))
                self.writable()
                response=self.close(kind,text)
                self.assertEqual(response.status_code,303)
                self.assertFalse(st.is_open)
                self.assertEqual(st.closing_revision_text,text or None)
                if text:
                    self.assertEqual(st.closing_revision_by_id,3)
                    self.assertIsNotNone(st.closing_revision_at)
                self.assertEqual(original,(self.f.draft.__dict__,self.f.amend.__dict__))
                log=self.ns['slog'].call_args.kwargs
                self.assertEqual(log['action'],'PFLOOR_DISCUSSION_CLOSED')
                self.assertEqual(log['details']['has_closing_revision'],bool(text))
                if text:self.assertNotIn(text,json.dumps(log['details']))

    def test_participant_cannot_close_or_set_revision(self):
        for kind in ['draft','amendment']:
            with self.assertRaises(HTTPException) as e:self.close(kind,'private',False)
            self.assertEqual(e.exception.status_code,403)
            self.assertIsNone(self.f.initial_proposal.closing_revision_text)
            self.assertIsNone(self.f.initial_amendment.closing_revision_text)

    def test_repeated_close_cannot_replace_recorded_revision(self):
        self.writable();self.close(text='First final text')
        with self.assertRaises(HTTPException) as e:self.close(text='Overwrite')
        self.assertEqual(e.exception.status_code,409)
        self.assertEqual(self.f.initial_proposal.closing_revision_text,'First final text')

    def test_closed_mutations_reject_before_writes_for_both_scopes(self):
        for kind,ident,st in [('draft',40,self.f.initial_proposal),('amendment',50,self.f.initial_amendment)]:
            st.is_open=False
            for name in ['pfloor_call_next','pfloor_finish_current','pfloor_take_now','pfloor_early_open',
                         'pfloor_early_close','pfloor_early_vote','pfloor_formal_open','pfloor_formal_close','pfloor_formal_vote','pfloor_withdraw']:
                args=dict(kind=kind,id=ident,event_id=10,request=self.request())
                if name.endswith('_vote'):args['choice']='YES'
                with self.subTest(kind=kind,route=name),self.assertRaises(HTTPException) as e:
                    self.ns[name](**args)
                self.assertEqual(e.exception.status_code,409)
            for chair in [False,True]:
                with self.assertRaises(HTTPException) as e:
                    self.ns['proposal_floor_post_intervention'](10,self.request(chair),kind,ident,body='Unsent',relates_to_id='')
                self.assertEqual(e.exception.status_code,409)
            for request_kind in ['GENERAL','ROR','ROR_ALL']:
                with self.assertRaises(HTTPException) as e:
                    asyncio.run(self.ns['pfloor_register'](10,kind,ident,self.request(False,form={'kind_req':request_kind})))
                self.assertEqual(e.exception.status_code,409)
            with self.assertRaises(HTTPException) as e:
                self.ns['pfloor_invite_ror'](10,kind,ident,self.request(),to_handle='viewer')
            self.assertEqual(e.exception.status_code,409)
        with self.assertRaises(HTTPException):self.ns['pfloor_invite_sponsors'](10,40,self.request())
        for name in ['amendment_vote','amendment_vote_open','amendment_vote_close']:
            args=dict(event_id=10,amend_id=50,request=self.request())
            if name=='amendment_vote':args['choice']='YES'
            with self.assertRaises(HTTPException) as e:self.ns[name](**args)
            self.assertEqual(e.exception.status_code,409)

    def test_closed_invitation_cannot_enqueue(self):
        self.f.initial_amendment.is_open=False
        with self.assertRaises(HTTPException) as e:
            self.ns['_pfloor_enqueue_from_invite'](self.f.db,N(),event_id=10,proposal_id=20,draft_id=None,amendment_id=50)
        self.assertEqual(e.exception.status_code,409)

    def test_confirmation_is_readonly_and_optional(self):
        for kind,ident in [('draft',40),('amendment',50)]:
            response=self.ns['pfloor_close_confirmation'](10,kind,ident,self.request(locale='ja'))
            self.assertEqual(response.status_code,200)
            self.assertIn('name="closing_revision"',response.body.decode())
            self.assertIn('任意',response.body.decode())
            self.assertNotIn('required',response.body.decode().split('id="closing-revision"')[1].split('</textarea>')[0])
            with self.assertRaises(HTTPException) as e:
                self.ns['pfloor_close_confirmation'](10,kind,ident,self.request(False))
            self.assertEqual(e.exception.status_code,403)

    def test_decision_surfaces_revisions_without_changing_outcomes(self):
        for st in [self.f.initial_proposal,self.f.initial_amendment]:
            st.is_open=False;st.closing_revision_text='Recorded only';st.closing_revision_at=self.f.now
        self.f.draft.submitted_at=self.f.now
        self.f.vote('Formal',is_open=False,yes=2,no=2)
        response=self.ns['decision_page'](10,self.request(locale='ja'))
        html=response.body.decode()
        self.assertEqual(html.count('Recorded only'),2)
        self.assertIn('不採択',html)
        self.assertEqual(response.context['results_drafts'][0]['outcome'],'Not adopted')
        self.assertEqual(self.f.draft.status,'TABLED')

    def test_index_separates_amendment_from_parent_floor(self):
        self.f.initial_proposal.is_open=True;self.f.initial_amendment.is_open=False
        self.f.prop.created_at=self.f.now
        self.ns['ProposalDraftStatus']=N(TABLED='TABLED',REINTRODUCED='REINTRODUCED',ADOPTED='ADOPTED')
        response=self.ns['proposal_floor_index'](10,self.request(False))
        self.assertEqual(len(response.context['open_rows']),1)
        self.assertEqual(len(response.context['closed_amendment_rows']),1)
        html=response.body.decode().split('id="proposal-floor-index-tour-closed"')[1]
        self.assertIn('/proposal-floor/amendment/50',html)
        self.assertIn('View Discussion',html)

    def test_existing_privileged_reopening_preserves_revision(self):
        self.writable()
        self.close(text='Preserved revision')
        with self.assertRaises(HTTPException) as e:
            self.ns['pfloor_toggle']('draft',40,self.request(False),10)
        self.assertEqual(e.exception.status_code,403)
        response=self.ns['pfloor_toggle']('draft',40,self.request(),10)
        self.assertEqual(response.status_code,303)
        self.assertTrue(self.f.initial_proposal.is_open)
        self.assertEqual(self.f.initial_proposal.closing_revision_text,'Preserved revision')
        with self.assertRaises(HTTPException):self.close(text='Replacement')
        self.assertTrue(self.f.initial_proposal.is_open)
        self.close()
        self.assertFalse(self.f.initial_proposal.is_open)
        self.assertEqual(self.f.initial_proposal.closing_revision_text,'Preserved revision')

    def test_closure_preserves_existing_adoption_and_tie_rules(self):
        self.writable()
        vote=self.f.vote('Formal',is_open=False,yes=2,no=2,abstain=99)
        self.close(text='This wording alone does not adopt anything')
        self.assertEqual(self.f.draft.status,'TABLED')
        self.f.initial_proposal.is_open=True
        vote.yes=3
        self.close()
        self.assertEqual(self.f.draft.status,'ADOPTED')
        self.assertEqual((vote.yes,vote.no,vote.abstain),(3,2,99))
