"""Read-only route tests using the existing isolated-source unittest pattern.

No app.main/app.db imports, application startup, SQL engine, network, or model
initialization. The in-memory query double evaluates the handlers' predicates;
any attempted persistence or AI call fails the test.
"""
import ast
import asyncio
import time
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import re
from types import SimpleNamespace as N
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from app.i18n import install_jinja

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


class Condition:
    def __init__(self, fn): self.fn = fn
    def __and__(self, other): return Condition(lambda row: self.fn(row) and other.fn(row))
    def __or__(self, other): return Condition(lambda row: self.fn(row) or other.fn(row))


class Column:
    def __init__(self, model, name): self.model, self.name = model, name
    def __eq__(self, value): return Condition(lambda row: getattr(row, self.name, None) == value)
    def __ge__(self, value): return Condition(lambda row: getattr(row, self.name, None) >= value)
    def in_(self, values): return Condition(lambda row: getattr(row, self.name, None) in values)
    def is_(self, value): return self == value
    def isnot(self, value): return Condition(lambda row: getattr(row, self.name, None) != value)
    def asc(self): return self
    def desc(self): return self


class ModelType(type):
    def __getattr__(cls, name): return Column(cls, name)


class Row(metaclass=ModelType):
    def __init__(self, **values): self.__dict__.update(values)


class Aggregate:
    def __init__(self, kind, column): self.kind, self.column, self.model = kind, column, column.model


class Query:
    def __init__(self, *columns):
        self.columns = columns
        self.model = columns[0] if isinstance(columns[0], ModelType) else columns[0].model
        self.conditions, self.order = [], []
    def where(self, *conditions): self.conditions.extend(conditions); return self
    def options(self, *args): return self
    def order_by(self, *columns): self.order = columns; return self


class Results:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows
    def first(self): return self.rows[0] if self.rows else None
    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]


class ReadOnlyDB:
    def __init__(self, rows): self.rows = rows
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def get(self, model, ident):
        return next((r for r in self.rows if type(r) is model and r.id == ident), None)
    def exec(self, query):
        rows = [r for r in self.rows if type(r) is query.model and all(c.fn(r) for c in query.conditions)]
        for col in reversed(query.order):
            if isinstance(col, Column): rows.sort(key=lambda r: getattr(r, col.name))
        if isinstance(query.columns[0], Aggregate):
            values = [len(rows) if a.kind == 'count' else max((getattr(r, a.column.name) for r in rows), default=None)
                      for a in query.columns]
            return Results([tuple(values) if len(values) > 1 else values[0]])
        if isinstance(query.columns[0], Column):
            return Results([getattr(r, query.columns[0].name) for r in rows])
        return Results(rows)
    def __getattr__(self, name):
        raise AssertionError('Unexpected database operation: ' + name)


def flags(user):
    roles = user.role_names if user else []
    return {key: role in roles for key, role in [('IS_ADMIN','admin'), ('IS_CHAIR','chairman'),
        ('IS_PRESIDENT','president'), ('IS_MEMBER','member'), ('IS_INVITED','invited speaker')]}


class PollingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        names = ['User','Event','EventAccessGrant','AgendaProposal','GeneralFloorLink','Question',
                 'FloorState','SpeakerRequest','Intervention','ProposalRoom','ProposalMessage',
                 'ProposalDraft','Amendment','ProposalFloorState','ProposalSpeakerRequest',
                 'ProposalIntervention','ProposalEarlyVote','ProposalFormalVote','ProposalEarlyBallot','ProposalFormalBallot']
        self.models = {name: ModelType(name, (Row,), {}) for name in names}
        self.rows = []
        def add(name, **values):
            row = self.models[name](**values); self.rows.append(row); return row
        self.add = add
        self.user = add('User', id=2, handle='viewer', role_names=['member'], roles=[])
        add('User', id=1, handle='author', role_names=['member'], roles=[])
        add('User', id=3, handle='chair', role_names=['chairman'], roles=[])
        add('User', id=4, handle='banned', role_names=['member','banned'], roles=[])
        self.event = add('Event', id=10, access_mode='open', stages=[])
        self.prop = add('AgendaProposal', id=20, event_id=10, status='accepted', title='Agenda',
                        background='Context', source_url=None, created_at=self.now, proposer=None)
        self.room = add('ProposalRoom', id=30, event_id=10, proposal_id=20, sponsor_id=1)
        self.draft = add('ProposalDraft', id=40, event_id=10, proposal_id=20, room_id=30,
            is_submitted=True, submitted_at=self.now-timedelta(days=1), status='TABLED', l_number='L.1', title='Original')
        self.amend = add('Amendment', id=50, draft_id=40, am_no=1, label='L.1/Amend.1', body_markdown='Amendment text')
        self.db = ReadOnlyDB(self.rows)
        self.ai = Mock(side_effect=AssertionError('Unexpected AI/translation call'))
        self.templates = Jinja2Templates(directory=str(ROOT/'app/templates'))
        install_jinja(self.templates.env)
        self.templates.env.globals.update(getattr=getattr, ai_features_enabled=False)
        self.ns = dict(self.models, __name__='isolated_polling', Request=Request, HTTPException=HTTPException,
            JSONResponse=JSONResponse, templates=self.templates, select=Query, selectinload=lambda *a: None,
            func=N(count=lambda c: Aggregate('count',c), max=lambda c: Aggregate('max',c)),
            and_=lambda *cs: Condition(lambda row: all(c.fn(row) for c in cs)),
            get_session=lambda: self.db, effective_flags=flags,
            unsign_cookie=lambda value: int(value) if value else None,
            has_role=lambda user, role: role in user.role_names,
            ProposalStatus=N(accepted='accepted'), EventAccessMode=N(open='open'),
            datetime=datetime, timezone=timezone, VISIBLE_AFTER=timedelta(0), re=re,
            _now_utc=lambda: self.now, AI_FEATURES_ENABLED=False,
            ensure_general_floor_question=self.ai, get_or_create_floor=self.ai,
            _pfloor_get_or_create_state=self.ai, translate_text=self.ai, mark_dirty=self.ai,
            refresh_summary_if_needed=self.ai, get_cached_or_enqueue_draft_translation=self.ai,
            DRAFT_GENERATION_EXECUTOR=self.ai)
        # The existing scope helper imports SQLAlchemy locally. Supply the same
        # predicate double there; do not import an engine or application module.
        sqlalchemy = patch.dict('sys.modules', {'sqlalchemy': N(and_=self.ns['and_'])})
        sqlalchemy.start()
        self.addCleanup(sqlalchemy.stop)
        needed = ['current_user','_is_event_member','_get_event_or_404','_user_has_event_access','_require_event_access',
            '_get_event_proposal_or_404','_get_event_room_or_404','_get_event_draft_or_404','_get_event_amendment_or_404',
            '_general_floor_item','_general_poll_context','_discussion_revision','_rows_revision','_thread_rows',
            '_floor_snapshot','_poll_json','interventions_fragment','interventions_head','floor_state_for_agenda',
            'room_updates','_pfloor_poll_context','_pfloor_voting_context','_amendment_vote_cards',
            'pfloor_interventions_fragment','pfloor_interventions_head','pfloor_state','_pfi_scope_where',
            '_get_early_vote','_get_formal_vote','_has_user_early_voted','_has_user_formal_voted',
            '_pf_user_has_floor','_pfloor_load_speakers','_to_aware_utc','_is_shared_poll_read',
            '_format_jst','experiment_session_log_middleware',
            'show_general_floor_item','proposal_floor_draft','proposal_floor_amendment']
        tree = ast.parse((ROOT/'app/main.py').read_text())
        definitions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef,ast.AsyncFunctionDef))}
        nodes = []
        for name in needed:
            node = copy.deepcopy(definitions[name]); node.decorator_list = []; nodes.append(node)
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, 'isolated_polling', 'exec'), self.ns)
        self.templates.env.filters['jst'] = self.ns['_format_jst']
        self.ns['ordered_speakers'] = lambda db,qid: [r for r in self.rows if type(r) is self.models['SpeakerRequest'] and r.question_id==qid]
        self.ns['_pfloor_queue_order'] = lambda: (self.models['ProposalSpeakerRequest'].position.asc(),)
        self.app = FastAPI()
        for route, name in [
            ('/events/{event_id}/general-floor/{pid}/floor/state','floor_state_for_agenda'),
            ('/events/{event_id}/general-floor/{pid}/interventions/head','interventions_head'),
            ('/events/{event_id}/general-floor/{pid}/interventions/fragment','interventions_fragment'),
            ('/events/{event_id}/proposal-floor/{kind}/{id}/floor/state','pfloor_state'),
            ('/events/{event_id}/proposal-floor/{kind}/{item_id}/interventions/head','pfloor_interventions_head'),
            ('/events/{event_id}/proposal-floor/{kind}/{item_id}/interventions/fragment','pfloor_interventions_fragment'),
            ('/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/updates','room_updates')]:
            self.app.add_api_route(route,self.ns[name],methods=['GET'])
        self.client = TestClient(self.app)
        self.client.cookies.set('session','2')
        self.general = '/events/10/general-floor/20'
        self.floor = '/events/10/proposal-floor/draft/40'
        self.room_url = '/events/10/proposal-discussion/20/rooms/30/updates'

    def tearDown(self): self.ai.assert_not_called()

    def link_general(self):
        self.add('Question',id=60,event_id=10)
        self.add('GeneralFloorLink',id=1,proposal_id=20,question_id=60)

    def intervention(self, ident, parent=None):
        return self.add('Intervention',id=ident,question_id=60,by_user=1,local_no=ident,
                        relates_to_id=parent,body='<script>unsafe</script>',created_at=self.now)

    def vote(self, kind, **values):
        defaults = dict(id=1,event_id=10,proposal_id=20,draft_id=40,amendment_id=None,
                        is_open=True,yes=2,no=1,abstain=3)
        defaults.update(values)
        return self.add('Proposal'+kind+'Vote',**defaults)

    def initial_floor_page(self, proposal=False, amendment=False, locale='en', role='member', populated=False, recognized=False):
        # Reuse existing persisted state in the read-only DB double; full-page
        # get-or-create helpers are stubbed only here, never in polling handlers.
        if not getattr(self, '_initial_setup', False):
            self._initial_setup = True
            self.link_general()
            self.initial_general = self.add('FloorState',id=60,question_id=60,is_open=True,
                speaking_time_sec=120,current_speaker_request_id=None)
            self.initial_proposal = self.add('ProposalFloorState',id=70,event_id=10,proposal_id=20,
                draft_id=40,amendment_id=None,is_open=True,speaking_time_sec=120,current_speaker_request_id=None)
            self.initial_amendment = self.add('ProposalFloorState',id=80,event_id=10,proposal_id=20,
                draft_id=None,amendment_id=50,is_open=True,speaking_time_sec=120,current_speaker_request_id=None)
            self.templates.env.globals['url_for'] = lambda name, **params: '/static/'+params.get('path','')
            for field in ['recalling','noting','welcoming','expressing_regret','expressing_deep_concern',
                          'emphasizing','decides','requests','calls_upon','encourages']:
                setattr(self.draft,field,'')
            self.draft.sponsor_id=1
        state = self.initial_amendment if amendment else self.initial_proposal if proposal else self.initial_general
        user_id = 3 if role == 'chair' else 2
        model = 'ProposalSpeakerRequest' if proposal else 'SpeakerRequest'
        if populated and not any(type(row) is self.models[model] for row in self.rows):
            for idx in range(1,13):
                self.add(model,id=idx,question_id=60,event_id=10,proposal_id=20,
                    draft_id=None if amendment else 40,amendment_id=50 if amendment else None,
                    user_id=2 if idx==1 else 1,position=idx,kind=['GENERAL','ROR','ROR_ALL'][idx%3],
                    status='SPEAKING' if recognized and idx==1 else 'QUEUED',created_at=self.now)
            if recognized: state.current_speaker_request_id=1
        request=Request({'type':'http','method':'GET','path':self.floor,'headers':[
            (b'cookie',f'session={user_id}; ui_locale=en'.encode()),(b'x-ui-language',locale.encode())]})
        request.state.help_ctx={}
        self.client.cookies.set('session',str(user_id))
        with patch.dict(self.ns, ensure_general_floor_question=lambda db,prop:self.db.get(self.models['Question'],60),
                        get_or_create_floor=lambda db,qid:self.initial_general,
                        _pfloor_get_or_create_state=lambda db,**scope:state):
            if amendment:
                response=self.ns['proposal_floor_amendment'](event_id=10,amendment_id=50,request=request)
                base='/events/10/proposal-floor/amendment/50'
            elif proposal:
                response=self.ns['proposal_floor_draft'](event_id=10,draft_id=40,request=request)
                base=self.floor
            else:
                response=self.ns['show_general_floor_item'](event_id=10,pid=20,request=request)
                base=self.general
        polled=self.client.get(base+'/floor/state',headers={'X-UI-Language':locale}).json()
        return response,polled,base

    def floor_dom(self, html):
        from html.parser import HTMLParser
        class Parser(HTMLParser):
            def __init__(self):
                super().__init__(); self.root=dict(tag='root',attrs={},children=[],text=''); self.stack=[self.root]
            def handle_starttag(self,tag,attrs):
                node=dict(tag=tag,attrs=dict(attrs),children=[],text='')
                self.stack[-1]['children'].append(node)
                if tag not in ['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']:
                    self.stack.append(node)
            def handle_endtag(self,tag):
                for index in range(len(self.stack)-1,0,-1):
                    if self.stack[index]['tag']==tag:
                        self.stack=self.stack[:index];break
            def handle_data(self,data):
                for node in self.stack: node['text']+=data
        parser=Parser();parser.feed(html)
        def visit(node):
            yield node
            for child in node['children']:yield from visit(child)
        nodes=list(visit(parser.root))
        return parser.root,{node['attrs']['id']:node for node in nodes if 'id' in node['attrs']}

    def test_initial_floor_pages_match_first_state_and_canonical_empty_fragments(self):
        for proposal,amendment in [(False,False),(True,False),(True,True)]:
            for locale in ['en','ja']:
                response,data,base=self.initial_floor_page(proposal,amendment,locale)
                ctx=response.context
                for key,value in ctx['initial_floor'].items(): self.assertEqual(data[key],value,key)
                self.assertEqual(ctx['discussion_revision'],data['discussion_revision'])
                html=response.body.decode();dom,ids=self.floor_dom(html)
                fragment=self.client.get(base+'/interventions/fragment',headers={'X-UI-Language':locale}).text
                def empty(source):return re.search(r'<li data-empty-message.*?</li>',source,re.S)[0]
                self.assertEqual(empty(html),empty(fragment))
                self.assertIn('text-center',empty(html))
                self.assertNotIn('Loading',ids['floor-open-badge']['text'])
                self.assertNotIn('読み込み中',ids['floor-open-badge']['text'])
                self.assertTrue(ids['qb-left']['text'])
                self.assertTrue(ids['qb-now']['text'])
                self.assertNotIn('display:none',ids['btn-request-floor']['attrs'].get('style',''))
                self.assertIn('display:none',ids['now-playing']['attrs']['style'])
                self.assertIn('lg:flex',ids['now-playing']['attrs']['class'])
                self.assertIn('display:none',ids['composer-box']['attrs']['style'])
                if proposal:
                    self.assertEqual(ids['pf-voting']['text'],self.floor_dom('<div id="vote">'+data['voting_html']+'</div>')[1]['vote']['text'])

        self.initial_general.is_open=False
        for locale in ['en','ja']:
            response,data,_=self.initial_floor_page(locale=locale)
            _,ids=self.floor_dom(response.body.decode())
            from app.i18n import translate
            self.assertEqual(ids['floor-open-badge']['text'],translate(locale,'floor.closed'))
            self.assertIn('display:none',ids['btn-request-floor']['attrs']['style'])
            self.assertFalse(data['is_open'])

    def test_initial_proposal_discussion_scope_and_permissions_match_fragments(self):
        self.add('ProposalIntervention',id=1,event_id=10,proposal_id=20,draft_id=40,amendment_id=None,
                 parent_id=None,by_user=1,local_no=1,body='Draft root',created_at=self.now)
        self.add('ProposalIntervention',id=2,event_id=10,proposal_id=20,draft_id=40,amendment_id=None,
                 parent_id=1,by_user=2,local_no=2,body='Draft reply',created_at=self.now)
        self.add('ProposalIntervention',id=3,event_id=10,proposal_id=20,draft_id=None,amendment_id=50,
                 parent_id=None,by_user=1,local_no=1,body='Amendment only',created_at=self.now)
        for amendment in [False,True]:
            response,data,base=self.initial_floor_page(True,amendment,'ja')
            html=response.body.decode();fragment=self.client.get(base+'/interventions/fragment',headers={'X-UI-Language':'ja'}).text
            for source in [html,fragment]:
                self.assertIn('Amendment only' if amendment else 'Draft root',source)
                self.assertNotIn('Draft root' if amendment else 'Amendment only',source)
                self.assertIn('value="ROR"',source)
                if not amendment:self.assertIn('Draft reply',source)
            self.assertEqual(response.context['discussion_revision'],data['discussion_revision'])
        for role,visible in [('member',False),('chair',True)]:
            response,_,_=self.initial_floor_page(True,False,'ja',role)
            _,ids=self.floor_dom(response.body.decode())
            self.assertEqual('form-call-next' in ids,visible)
            self.assertEqual('display:none' not in ids['composer-box']['attrs']['style'],visible)

    def test_initial_proposal_content_keeps_poll_visibility_restrictions(self):
        self.initial_floor_page(True)
        for amendment in [False,True]:
            for submitted, at in [(False,self.now),(True,None),(True,self.now+timedelta(days=1))]:
                self.draft.is_submitted=submitted
                self.draft.submitted_at=at
                with self.assertRaises(HTTPException) as denied:
                    self.initial_floor_page(True,amendment)
                self.assertEqual(denied.exception.status_code,403)
        self.draft.is_submitted=True
        self.draft.submitted_at=self.now-timedelta(days=1)

    def test_initial_poll_lifecycle_executes_without_dom_replacement_or_queue_loading(self):
        import shutil,subprocess
        from app.i18n import CATALOGUES
        if not shutil.which('node'):self.skipTest('Node is required for the dependency-free JS lifecycle check')
        fixtures=[]
        # Each independent fixture gets its own DB double, including >10 queued
        # rows, current recognition and both privileged/member layouts.
        for proposal,amendment in [(False,False),(True,False),(True,True)]:
            for locale in ['en','ja']:
                for role,populated,recognized in [('member',False,False),('member',True,True),('chair',True,False)]:
                    case=PollingTests();case.setUp()
                    try:
                        response,data,_=case.initial_floor_page(proposal,amendment,locale,role,populated,recognized)
                        html=response.body.decode();dom,ids=case.floor_dom(html)
                        self.assertNotIn('display:none',ids['reply-chip']['attrs'].get('style',''))
                        if proposal:self.assertIn('  wireReplyLinks();',html)
                        if role=='chair':
                            table=ids['table-nextup']
                            self.assertEqual(len(table['children'][0]['children']),10)
                            self.assertNotIn('hidden',ids['btn-more']['attrs'])
                        if recognized:
                            self.assertNotIn('display:none',ids['recognized-banner']['attrs']['style'])
                            self.assertNotIn('display:none',ids['composer-box']['attrs']['style'])
                        # Read the literal seeds emitted by the actual page.
                        state=json.loads(re.search(r'initialState: (.*),\n',html)[1])
                        revision=json.loads(re.search(r'initialRevision: (.*),\n',html)[1])
                        voting=json.loads(re.search(r'initialVoting: (.*)\n',html)[1]) if proposal else None
                        if proposal:self.assertEqual(voting,data['voting_html'])
                        fixtures.append(dict(dom=dom,locale=locale,catalogue=dict(CATALOGUES[locale]),
                            state=state,revision=revision,voting=voting,proposal=proposal,
                            userId=response.context['user'].id,list='interventions' if proposal else 'threads-container'))
                    finally:
                        case.tearDown();case.doCleanups();case.client.close()
        result=subprocess.run(['node','tests/floor_initial_lifecycle.js'],input=json.dumps(fixtures),
                              text=True,capture_output=True,cwd=ROOT,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS: 18 initial/poll lifecycles',result.stdout)

    def test_general_fragment_has_authoritative_item_context_and_keys(self):
        self.link_general(); self.intervention(1); self.intervention(2,1)
        response = self.client.get(self.general+'/interventions/fragment')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('/events/10/general-floor/20/floor/register',response.text)
        self.assertIn('data-discussion-revision="2:2"',response.text)
        self.assertIn('id="replies-1"',response.text)
        self.assertIn('data-post-id="2"',response.text)
        self.assertNotIn('<script>unsafe',response.text)
        self.assertIn('&lt;script&gt;',response.text)
        self.client.cookies.set('session','3')
        response = self.client.get(self.general+'/interventions/fragment')
        self.assertIn('name="to_handle"',response.text)
        self.assertIn('/events/10/general-floor/20/floor/invite_ror',response.text)

    def test_general_fragment_locale_pinned_across_cookie_changes(self):
        self.link_general(); self.intervention(1); self.intervention(2,1)
        self.client.cookies.set('ui_locale','en')
        response=self.client.get(self.general+'/interventions/fragment',headers={'X-UI-Language':'ja'})
        self.assertEqual(response.status_code,200)
        self.assertIn('答弁権を申請',response.text)
        self.assertIn('返信1件を表示',response.text)
        self.assertIn('value="ROR"',response.text)
        self.assertIn('&lt;script&gt;unsafe&lt;/script&gt;',response.text)
        self.client.cookies.set('ui_locale','ja')
        response=self.client.get(self.general+'/interventions/fragment',headers={'X-UI-Language':'en'})
        self.assertIn('Request Right of Reply',response.text)

    def test_general_reads_do_not_create_missing_question_or_floor(self):
        for linked in [False,True]:
            if linked: self.link_general()
            before = len(self.rows)
            for suffix in ['/floor/state','/interventions/head','/interventions/fragment']:
                response=self.client.get(self.general+suffix)
                self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(len(self.rows),before)
        state=self.client.get(self.general+'/floor/state').json()
        self.assertFalse(state['is_open']); self.assertEqual(state['discussion_revision'],'0:0')

    def test_revision_detects_later_commit_with_lower_id(self):
        self.link_general(); self.intervention(10)
        first=self.client.get(self.general+'/interventions/head').json()
        self.intervention(9)
        second=self.client.get(self.general+'/interventions/head').json()
        self.assertEqual(first['last_any_id'],second['last_any_id'])
        self.assertNotEqual(first['revision'],second['revision'])

    def test_room_second_viewer_messages_replies_and_unchanged_revision(self):
        self.add('ProposalMessage',id=1,room_id=30,user_id=1,local_no=1,parent_id=None,body='Root',created_at=self.now)
        first=self.client.get(self.room_url).json()
        self.add('ProposalMessage',id=2,room_id=30,user_id=1,local_no=2,parent_id=1,body='Reply',created_at=self.now)
        second=self.client.get(self.room_url,params={'revision':first['revision']}).json()
        self.assertEqual(second['revision'],'2:2')
        self.assertIn('data-post-id="1"',second['html']);self.assertIn('data-post-id="2"',second['html'])
        self.assertIn('Reply',second['html']); self.assertIn('@author',second['html'])
        unchanged=self.client.get(self.room_url,params={'revision':second['revision']}).json()
        self.assertIsNone(unchanged['html'])
        self.rows.remove(self.draft)
        self.assertEqual(self.client.get(self.room_url).status_code,200)
        self.assertFalse(any(type(r) is self.models['ProposalDraft'] for r in self.rows))

    def test_room_shared_draft_revision_tracks_save_cosign_and_submission(self):
        self.client.cookies.set('session', '2')
        self.room.sponsor_id = 1
        self.draft.sponsor_id = 1

        self.draft.title = 'Initial saved title'
        self.draft.cosigners_json = []
        self.draft.is_submitted = False
        self.draft.l_number = None
        self.draft.submitted_at = None

        first = self.client.get(self.room_url).json()
        self.assertTrue(first['draft_revision'])
        self.assertIsNotNone(first['draft_html'])
        self.assertIn('Initial saved title', first['draft_html'])

        unchanged = self.client.get(
            self.room_url,
            params={
                'revision': first['revision'],
                'draft_revision': first['draft_revision'],
            },
        ).json()
        self.assertIsNone(unchanged['html'])
        self.assertIsNone(unchanged['draft_html'])
        self.assertEqual(
            unchanged['draft_revision'],
            first['draft_revision'],
        )

        # A saved draft change must produce a new shared-state revision.
        self.draft.title = 'Updated saved title'
        saved = self.client.get(
            self.room_url,
            params={
                'revision': unchanged['revision'],
                'draft_revision': unchanged['draft_revision'],
            },
        ).json()
        self.assertNotEqual(
            saved['draft_revision'],
            unchanged['draft_revision'],
        )
        self.assertIsNotNone(saved['draft_html'])
        self.assertIn('Updated saved title', saved['draft_html'])

        # A co-sign change must also invalidate the shared-state fragment.
        self.draft.cosigners_json = [2]
        cosigned = self.client.get(
            self.room_url,
            params={
                'revision': saved['revision'],
                'draft_revision': saved['draft_revision'],
            },
        ).json()
        self.assertNotEqual(
            cosigned['draft_revision'],
            saved['draft_revision'],
        )
        self.assertIsNotNone(cosigned['draft_html'])
        self.assertIn('Remove co-sign', cosigned['draft_html'])

        # Submission/L-number changes must propagate without a page reload.
        self.draft.is_submitted = True
        self.draft.l_number = 'L.77'
        self.draft.submitted_at = self.now

        submitted = self.client.get(
            self.room_url,
            params={
                'revision': cosigned['revision'],
                'draft_revision': cosigned['draft_revision'],
            },
        ).json()
        self.assertNotEqual(
            submitted['draft_revision'],
            cosigned['draft_revision'],
        )
        self.assertIsNotNone(submitted['draft_html'])
        self.assertIn('L.77', submitted['draft_html'])
        self.assertIn('Submitted', submitted['draft_html'])
        self.assertIn('2026-01-02 09:00 JST', submitted['draft_html'])
        self.assertIn('Co-signing is closed', submitted['draft_html'])

    def test_jst_display_and_internal_message_numbers_are_hidden(self):
        # Stored UTC timestamps are displayed as JST.
        self.assertEqual(
            self.ns['_format_jst'](
                datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)
            ),
            '2026-01-02 09:00 JST',
        )

        # Naive datetimes in this codebase are UTC as well.
        self.assertEqual(
            self.ns['_format_jst'](
                datetime(2026, 1, 2, 0, 0)
            ),
            '2026-01-02 09:00 JST',
        )

        # Proposal-room messages must show JST but not their local sequence ID.
        self.add(
            'ProposalMessage',
            id=68,
            room_id=30,
            user_id=1,
            local_no=68,
            parent_id=None,
            body='Number visibility test',
            created_at=datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc),
        )

        room = self.client.get(self.room_url).json()
        html = room['html']

        # Room chat uses a date separator plus compact JST time.
        self.assertIn('Fri, January 2, 2026', html)
        self.assertIn('9:00 AM', html)
        self.assertIn('@author', html)

        # Internal sequence IDs and reply-thread labels stay hidden.
        self.assertNotIn('#68', html)
        self.assertNotIn('Replying to', html)
        self.assertNotIn('data-room-reply', html)

        # General/Proposal Floor templates must not expose internal numbering.
        for relative in (
            'app/templates/macros/interventions.html',
            'app/templates/macros/interventions-prop.html',
            'app/templates/partials/proposal_interventions_list.html',
        ):
            source = (ROOT / relative).read_text()
            self.assertNotIn('#{{ it.local_no', source)
            self.assertNotIn('↩', source)
            self.assertNotIn('to #', source)

    def test_room_poll_fragment_has_request_and_current_user_context(self):
        source = (ROOT / 'app/main.py').read_text()

        start = source.index('def room_updates(')
        end = source.index('\n\nimport sqlalchemy as sa', start)
        room_source = source[start:end]

        self.assertIn(
            'request=request,\n                user=user,\n                threads=',
            room_source,
        )
        self.assertIn(
            'request=request,\n                    user=user,',
            room_source,
        )

    def test_room_drafting_guidance_is_shared_with_non_sponsors(self):
        room_source = (
            ROOT / 'app/templates/rooms/show.html'
        ).read_text()

        shared_source = (
            ROOT / 'app/templates/partials/room_shared_state.html'
        ).read_text()

        # The crash course must sit outside the sponsor-only editor block.
        crash = room_source.index("t('draft.guide')")
        sponsor_gate = room_source.index(
            '{% if is_sponsor and not draft.is_submitted %}',
            crash,
        )
        self.assertLess(crash, sponsor_gate)

        self.assertIn(
            "t('draft.guide_structure')",
            room_source,
        )
        self.assertIn(
            "t('draft.guidance_tip')",
            room_source,
        )

        # Read-only participants get the same clause-level explanations.
        self.assertIn(
            'data-draft-hint="{{ description }}"',
            shared_source,
        )
        self.assertIn(
            "t('draft.preambular')",
            shared_source,
        )
        self.assertIn(
            "t('draft.operative')",
            shared_source,
        )

    def test_phase6_room_poll_pins_japanese_for_messages_and_shared_draft(self):
        from app.i18n import translate
        self.client.cookies.set('ui_locale', 'en')
        self.draft.is_submitted = False
        self.draft.cosigners_json = []
        self.draft.recalling = 'Author background 日本語'
        self.draft.decides = 'Calls upon all participants to...'
        first = self.client.get(self.room_url, headers={'X-UI-Language': 'ja'}).json()
        self.assertIn('討議を始めましょう', first['html'])
        for label in ['保存済み作業草案', '前文条項', '主文条項', '提案者', '共同署名者', '未使用']:
            self.assertIn(label, first['draft_html'])
        for key in ['recalling', 'noting', 'decides', 'encourages']:
            self.assertIn(translate('ja', 'draft.guidance.'+key), first['draft_html'])
        self.add('ProposalMessage', id=1, room_id=30, user_id=1, local_no=1,
                 parent_id=None, body='Chat 日本語 <unchanged>', created_at=self.now)
        self.draft.title = 'New authored title 日本語'
        self.draft.cosigners_json = [2]
        second = self.client.get(self.room_url, headers={'X-UI-Language': 'ja'}, params={
            'revision': first['revision'], 'draft_revision': first['draft_revision']}).json()
        self.assertIn('Chat 日本語 &lt;unchanged&gt;', second['html'])
        self.assertIn('@author', second['html'])
        self.assertIn('共同署名を取り消す', second['draft_html'])
        for authored in [self.draft.title, self.draft.recalling, self.draft.decides]:
            self.assertIn(authored, second['draft_html'])
        self.draft.is_submitted = True
        third = self.client.get(self.room_url, headers={'X-UI-Language': 'ja'}, params={
            'revision': second['revision'], 'draft_revision': second['draft_revision']}).json()
        self.assertIn('提出済み', third['draft_html'])
        self.assertIn('共同署名の受付は終了しました', third['draft_html'])
        self.assertNotIn('/draft/cosign"', third['draft_html'])
        self.assertEqual(self.draft.status, 'TABLED')
        # A second English document remains English despite a Japanese cookie.
        self.client.cookies.set('ui_locale', 'ja')
        english = self.client.get(self.room_url, headers={'X-UI-Language': 'en'}).json()
        self.assertIn('Preambular clauses', english['draft_html'])
        self.assertEqual(english['html'], second['html'])  # Authored chat and timestamps unchanged.
        self.assertEqual(english['draft_revision'], third['draft_revision'])
        self.assertEqual(english['revision'], second['revision'])

    def test_phase6_room_poll_preserves_sponsor_editor_boundary(self):
        self.draft.is_submitted = False
        self.client.cookies.set('session', '1')
        html = self.client.get(self.room_url, headers={'X-UI-Language': 'ja'}).json()['draft_html']
        self.assertIn('提案者', html)
        self.assertNotIn('draft-readonly-document', html)
        self.assertNotIn('id="draft_title"', html)
        self.assertNotIn('/draft/cosign"', html)
        self.ai.assert_not_called()

    def test_room_access_and_relations(self):
        self.client.cookies.clear();self.assertEqual(self.client.get(self.room_url).status_code,401)
        self.client.cookies.set('session','4');self.assertEqual(self.client.get(self.room_url).status_code,403)
        self.client.cookies.set('session','2')
        self.event.access_mode='passcode'
        self.assertEqual(self.client.get(self.room_url).status_code,403)
        self.add('EventAccessGrant',id=1,event_id=10,user_id=2)
        self.assertEqual(self.client.get(self.room_url).status_code,200)
        self.room.proposal_id=99;self.assertEqual(self.client.get(self.room_url).status_code,404)
        self.room.proposal_id=20;self.room.event_id=99;self.assertEqual(self.client.get(self.room_url).status_code,404)
        self.room.event_id=10;self.prop.event_id=99;self.assertEqual(self.client.get(self.room_url).status_code,404)
        self.prop.event_id=10;self.prop.status='rejected';self.assertEqual(self.client.get(self.room_url).status_code,404)

    def test_floor_readonly_missing_state_and_scope_separation(self):
        self.add('ProposalIntervention',id=1,event_id=10,proposal_id=20,draft_id=40,amendment_id=None,
                 by_user=1,parent_id=None,local_no=1,body='Draft discussion',created_at=self.now)
        self.add('ProposalIntervention',id=2,event_id=10,proposal_id=20,draft_id=None,amendment_id=50,
                 by_user=1,parent_id=None,local_no=1,body='Amendment discussion',created_at=self.now)
        for kind,ident,own,other in [('draft',40,'Draft discussion','Amendment discussion'),('amendment',50,'Amendment discussion','Draft discussion')]:
            base=f'/events/10/proposal-floor/{kind}/{ident}'
            for suffix in ['/floor/state','/interventions/head','/interventions/fragment']:
                response=self.client.get(base+suffix)
                self.assertEqual(response.status_code,200,response.text)
                if suffix.endswith('fragment'):
                    self.assertIn(own,response.text);self.assertNotIn(other,response.text)
        self.assertFalse(any(type(r) is self.models['ProposalFloorState'] for r in self.rows))

    def test_floor_access_visibility(self):
        for base in [self.general,self.floor]:
            self.client.cookies.clear();self.assertEqual(self.client.get(base+'/floor/state').status_code,401)
            self.client.cookies.set('session','4');self.assertEqual(self.client.get(base+'/floor/state').status_code,403)
            self.client.cookies.set('session','2')
        self.draft.is_submitted=False;self.assertEqual(self.client.get(self.floor+'/floor/state').status_code,403)
        self.draft.is_submitted=True;self.draft.event_id=99
        self.assertEqual(self.client.get('/events/10/proposal-floor/amendment/50/floor/state').status_code,404)
        self.draft.event_id=10;self.amend.draft_id=99
        self.assertEqual(self.client.get('/events/10/proposal-floor/amendment/50/interventions/fragment').status_code,404)
        self.prop.status='rejected';self.assertEqual(self.client.get(self.floor+'/floor/state').status_code,404)

    def voting_html(self, url=None):
        response=self.client.get((url or self.floor)+'/floor/state')
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['voting_html']

    def test_early_vote_unopened_open_voted_totals_closed(self):
        self.assertIn('Early voting has not been opened yet',self.voting_html())
        vote=self.vote('Early')
        html=self.voting_html()
        self.assertIn('/early/vote',html);self.assertIn('<strong>3</strong> Abstain',html)
        self.add('ProposalEarlyBallot',id=1,event_id=10,proposal_id=20,draft_id=40,amendment_id=None,user_id=1,choice='NO')
        self.assertIn('/early/vote',self.voting_html()) # Other user's ballot does not lock this user.
        self.add('ProposalEarlyBallot',id=2,event_id=10,proposal_id=20,draft_id=40,amendment_id=None,user_id=2,choice='YES')
        html=self.voting_html();self.assertIn('already cast your early vote',html);self.assertNotIn('/early/vote"',html)
        vote.is_open=False;vote.no=0
        self.assertIn('Adopted by consensus',self.voting_html())
        vote.no=2;vote.yes=0;self.assertIn('Rejected by consensus',self.voting_html())
        vote.yes=2;self.assertIn('No consensus',self.voting_html())

    def test_formal_vote_results_and_persisted_status(self):
        self.assertIn('Formal vote is not opened yet',self.voting_html())
        vote=self.vote('Formal')
        self.assertIn('/formal/vote',self.voting_html())
        self.add('ProposalFormalBallot',id=1,formal_vote_id=vote.id,user_id=2,choice='ABSTAIN')
        self.assertIn('already cast your formal vote',self.voting_html())
        vote.is_open=False;vote.yes=2;vote.no=2;vote.abstain=20
        html=self.voting_html();self.assertIn('Formal voting is closed',html);self.assertIn('Rejected',html)
        vote.yes=3;html=self.voting_html();self.assertIn('Accepted',html);self.assertIn('Counted: 5',html)
        self.draft.status='ADOPTED';self.assertIn('Draft status: ADOPTED',self.voting_html())

    def test_amendment_cards_use_active_votes_and_never_other_ballot_choices(self):
        vote=self.vote('Formal',draft_id=None,amendment_id=50,is_open=False,yes=4,no=1)
        html=self.voting_html();self.assertIn('L.1/Amend.1',html);self.assertIn('Adopted',html)
        self.add('ProposalFormalBallot',id=1,formal_vote_id=vote.id,user_id=1,choice='PRIVATE_SENTINEL')
        html=self.voting_html('/events/10/proposal-floor/amendment/50')
        self.assertNotIn('PRIVATE_SENTINEL',html)
        self.assertIn('/amendment/50/formal/open',self._chair_html())

    def _chair_html(self):
        self.client.cookies.set('session','3')
        return self.voting_html('/events/10/proposal-floor/amendment/50')

    def test_proposal_vote_polling_keeps_japanese_and_result_rules(self):
        self.client.cookies.set('ui_locale','en')
        vote=self.vote('Formal',yes=2,no=2,abstain=100)
        def poll():
            response=self.client.get(self.floor+'/floor/state',headers={'X-UI-Language':'ja'})
            self.assertEqual(response.status_code,200)
            return response.json()['voting_html']
        html=poll()
        for choice,label in [('YES','賛成'),('NO','反対'),('ABSTAIN','棄権')]:
            self.assertIn('value="'+choice+'"',html);self.assertIn(label,html)
        self.assertIn('上程済み',html)
        self.add('ProposalFormalBallot',id=1,formal_vote_id=vote.id,user_id=2,choice='ABSTAIN')
        html=poll()
        self.assertIn('正式投票は記録済みです。',html)
        self.assertNotIn('/formal/vote"',html)
        vote.is_open=False
        self.assertIn('否決',poll()) # Tie, regardless of abstentions.
        vote.yes=3
        self.assertIn('採択',poll())
        self.assertEqual((vote.yes,vote.no,vote.abstain),(3,2,100))
        self.assertEqual(self.draft.status,'TABLED')
        self.draft.status='ADOPTED'
        self.assertIn('草案の状態： 採択',poll())
        self.assertEqual(self.draft.status,'ADOPTED')
        self.client.cookies.set('ui_locale','ja')
        english=self.client.get(self.floor+'/floor/state',headers={'X-UI-Language':'en'}).json()['voting_html']
        self.assertIn('Draft status: ADOPTED',english)

    def test_japanese_consensus_preserves_zero_votes_and_abstention_rules(self):
        self.client.cookies.set('ui_locale','ja')
        vote=self.vote('Early',is_open=False,abstain=100)
        for yes,no,label in [(1,0,'合意により採択'),(0,1,'合意により否決'),
                             (0,0,'合意に至りませんでした'),(1,1,'合意に至りませんでした')]:
            vote.yes=yes;vote.no=no
            html=self.voting_html()
            self.assertIn(label,html)
            self.assertEqual((vote.yes,vote.no,vote.abstain),(yes,no,100))
        self.assertEqual(self.draft.status,'TABLED')

    def test_japanese_amendment_poll_preserves_formal_priority_and_scope(self):
        self.vote('Early',draft_id=None,amendment_id=50,is_open=False,yes=5,no=0)
        formal=self.vote('Formal',draft_id=None,amendment_id=50,is_open=False,yes=2,no=2,abstain=50)
        self.client.cookies.set('ui_locale','ja')
        html=self.voting_html()
        self.assertIn('否決',html)
        self.assertIn('L.1/Amend.1',html)
        self.assertEqual(formal.no,2)
        self.add('ProposalFormalBallot',id=1,formal_vote_id=formal.id,user_id=1,choice='PRIVATE_CHOICE')
        html=self.voting_html('/events/10/proposal-floor/amendment/50')
        self.assertNotIn('PRIVATE_CHOICE',html)
        self.assertNotIn('/draft/40/formal/vote',html)

    def test_logging_exemptions_are_explicit(self):
        check=self.ns['_is_shared_poll_read']
        for path in [self.room_url,self.general+'/floor/state',self.general+'/interventions/head',
                     self.floor+'/interventions/fragment']:
            self.assertTrue(check(path))
        for path in [self.general,self.floor+'/early/vote',self.floor+'/floor/register','/dashboard']:
            self.assertFalse(check(path))
        source=(ROOT/'app/main.py').read_text()
        self.assertIn('request.method == "GET" and _is_shared_poll_read(path)',source)

    def test_existing_floor_recognition_queue_and_readonly_snapshot(self):
        self.link_general()
        self.intervention(7)
        self.add('FloorState', id=1, question_id=60, is_open=True,
                 speaking_time_sec=120, current_speaker_request_id=8)
        self.add('SpeakerRequest', id=8, question_id=60, user_id=2, kind='ROR',
                 status='SPEAKING', position=1, target_intervention_id=7, created_at=self.now)
        self.add('ProposalFloorState', id=2, event_id=10, proposal_id=20,
                 draft_id=40, amendment_id=None, is_open=False, formal_is_open=False,
                 speaking_time_sec=90, current_speaker_request_id=9)
        self.add('ProposalSpeakerRequest', id=9, event_id=10, proposal_id=20,
                 draft_id=40, amendment_id=None, user_id=2, kind='ROR_ALL',
                 status='SPEAKING', position=1, target_intervention_id=None, created_at=self.now)
        self.add('ProposalSpeakerRequest', id=10, event_id=10, proposal_id=20,
                 draft_id=None, amendment_id=50, user_id=1, kind='GENERAL',
                 status='QUEUED', position=2, target_intervention_id=None, created_at=self.now)
        self.vote('Formal')  # Active record wins even though formal_is_open is false.
        before=copy.deepcopy([r.__dict__ for r in self.rows])
        general=self.client.get(self.general+'/floor/state').json()
        self.assertTrue(general['can_speak'])
        self.assertEqual(general['current_target_local_no'],7)
        proposal=self.client.get(self.floor+'/floor/state').json()
        self.assertEqual([r['id'] for r in proposal['speakers']],[9])
        self.assertEqual(proposal['current_kind'],'ROR_ALL')
        self.assertIn('/formal/vote',proposal['voting_html'])
        for base in [self.general,self.floor]:
            for suffix in ['/interventions/head','/interventions/fragment']:
                self.assertEqual(self.client.get(base+suffix).status_code,200)
        self.assertEqual([r.__dict__ for r in self.rows],before)

    def test_draft_ballot_does_not_lock_amendment_ballot(self):
        self.vote('Early')
        self.vote('Early',id=2,draft_id=None,amendment_id=50)
        self.add('ProposalEarlyBallot',id=1,event_id=10,proposal_id=20,draft_id=40,
                 amendment_id=None,user_id=2,choice='NO')
        self.assertIn('already cast your early vote',self.voting_html())
        html=self.voting_html('/events/10/proposal-floor/amendment/50')
        self.assertIn('/amendment/50/early/vote',html)
        self.assertNotIn('already cast your early vote',html)

    def test_logging_middleware_skips_poll_reads_but_logs_actions(self):
        session=MagicMock()
        self.ns.update(time=time, _session_log_should_skip=lambda path: False,
            get_or_make_session_key=lambda request: 'test-session', get_request_id=lambda request: 'test-request',
            get_session=session, add_session_log=Mock(), _log_user_id_from_request=lambda request: 2,
            _event_id_from_path=lambda path: 10)
        paths=[self.room_url]+[base+suffix for base in [self.general,self.floor]
            for suffix in ['/floor/state','/interventions/head','/interventions/fragment']]
        for path in paths:
            request=Request({'type':'http','method':'GET','path':path,'headers':[], 'query_string':b''})
            asyncio.run(self.ns['experiment_session_log_middleware'](request,AsyncMock(return_value=JSONResponse({}))))
        session.assert_not_called()
        self.ns['add_session_log'].assert_not_called()
        for method,path in [('POST',self.floor+'/formal/vote'),('GET',self.floor),('POST',self.floor+'/floor/state')]:
            request=Request({'type':'http','method':method,'path':path,'headers':[], 'query_string':b''})
            asyncio.run(self.ns['experiment_session_log_middleware'](request,AsyncMock(return_value=JSONResponse({}))))
        self.assertEqual(self.ns['add_session_log'].call_count,3)
        self.assertEqual(session.return_value.__enter__.return_value.commit.call_count,3)

    def test_all_poll_routes_enforce_membership_and_event_grants(self):
        paths=[self.room_url]+[base+suffix for base in [self.general,self.floor,
            '/events/10/proposal-floor/amendment/50']
            for suffix in ['/floor/state','/interventions/head','/interventions/fragment']]
        self.event.access_mode='passcode'
        for path in paths:
            self.assertEqual(self.client.get(path).status_code,403,path)
        self.add('EventAccessGrant',id=1,event_id=10,user_id=2)
        for path in paths:
            self.assertEqual(self.client.get(path).status_code,200,path)
        self.user.role_names=[]
        for path in paths:
            self.assertEqual(self.client.get(path).status_code,403,path)

    def test_complete_active_pages_render_with_ai_disabled(self):
        self.templates.env.globals['url_for'] = lambda name, **params: (
            '/static/' + params['path'] if name == 'static' else
            f"/events/{params['event_id']}/proposal-discussion/{params['pid']}/rooms/{params['rid']}/delete")
        self.draft.sponsor_id=1
        for field in ['recalling','noting','welcoming','expressing_regret','expressing_deep_concern',
                      'emphasizing','decides','requests','calls_upon','encourages']:
            setattr(self.draft,field,'')
        self.link_general()
        q=self.db.get(self.models['Question'],60)
        st=N(id=1,is_open=True,current_speaker_request_id=None,speaking_time_sec=120)
        for user_id in [1,2,3]:
            user=self.db.get(self.models['User'],user_id)
            context=dict(user=user,flags=flags(user),event=self.event,proposal=self.prop,
                room=self.room,draft=self.draft,amendment=self.amend,q=q,
                item=self.ns['_general_floor_item'](self.prop,q),floor=st,pfloor=st,
                threads=[],user_map={user.id:user},role_map={},speakers=[],
                can_speak=user_id==3,current_req=None,last_any_id=0,last_child_id=0,
                initial_floor=self.ns['_floor_snapshot'](self.db,st,[],user),discussion_revision="0:0",
                cos_list=[],early_vote=None,formal_vote=None,HAS_EARLY_VOTED=False,
                HAS_FORMAL_VOTED=False,amendment_cards=[])
            for name,mode in [('events/general_floor_item.html','DRAFT'),
                ('events/proposal_floor_item.html','DRAFT'),('events/proposal_floor_item.html','AMENDMENT'),
                ('rooms/show.html','DRAFT')]:
                with self.subTest(template=name,mode=mode,user=user_id):
                    request=Request({'type':'http','method':'GET','path':self.floor,'headers':[]})
                    self.draft.is_submitted = name != 'rooms/show.html'
                    request.state.help_ctx = {}
                    html=self.templates.env.get_template(name).render(request=request,mode=mode,**context)
                    self.assertIn('DeliberationPolling.',html)
                    self.assertIn('textarea',html)

    def test_templates_parse_and_controls_are_not_in_message_region(self):
        for name in ['base.html','rooms/show.html','events/general_floor_item.html','events/proposal_floor_item.html',
                     'partials/room_messages.html','partials/pfloor_vote_panel.html','macros/interventions.html','macros/interventions-prop.html']:
            self.templates.env.parse((ROOT/'app/templates'/name).read_text())
        html=self.client.get(self.room_url).json()['html']
        self.assertNotIn('draft_title',html);self.assertNotIn('textarea',html)
        self.assertNotIn('Generate',html)
        self.assertIn("it.type === 'INVITE_ROR_PFLOOR'",(ROOT/'app/templates/base.html').read_text())


if __name__ == '__main__': unittest.main()
