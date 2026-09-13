"""UI locale regressions; no app.main/db import, startup, or real database."""
import ast
import asyncio
import builtins
import json
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader

from app import i18n

ROOT = Path(__file__).resolve().parents[1]


def request(locale=None, cookie=None):
    headers = []
    if locale is not None:
        headers.append((b'x-ui-language', locale.encode()))
    if cookie is not None:
        headers.append((b'cookie', ('ui_locale=' + cookie).encode()))
    return Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': headers})


def isolated_functions():
    names = {'ui_locale_context', 'set_ui_language', '_format_jst'}
    nodes = [n for n in ast.parse((ROOT/'app/main.py').read_text()).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    ns = dict(Request=Request, Form=Form, RedirectResponse=RedirectResponse,
              resolve_locale=i18n.resolve_locale, normalize_locale=i18n.normalize_locale,
              safe_return_to=i18n.safe_return_to, UI_COOKIE_SECURE=False)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'isolated_ui_locale', 'exec'), ns)
    return ns


class LocaleTests(unittest.TestCase):
    def test_default_invalid_and_no_accept_language_negotiation(self):
        for value in [None, '', 'fr', 'JA', 'ja-JP']:
            self.assertEqual(i18n.normalize_locale(value), 'en')
            self.assertEqual(i18n.resolve_locale(request(value, value)), 'en')
        req = Request({'type': 'http', 'headers': [(b'accept-language', b'ja')]})
        self.assertEqual(i18n.resolve_locale(req), 'en')

    def test_header_cookie_priority(self):
        for header, cookie, expected in [(None, 'ja', 'ja'), ('en', 'ja', 'en'),
                ('ja', 'en', 'ja'), ('invalid', 'ja', 'ja'), ('ja', 'invalid', 'ja')]:
            self.assertEqual(i18n.resolve_locale(request(header, cookie)), expected)

    def test_missing_key_fallback_unknown_key_and_parameters(self):
        with patch.object(i18n, 'CATALOGUES', {'en': {'example': 'Hello {name}'}, 'ja': {}}):
            self.assertEqual(i18n.translate('ja', 'example', name='Alex'), 'Hello Alex')
            self.assertEqual(i18n.translate('ja', 'unknown.key'), 'unknown.key')
        self.assertEqual(i18n.translate('ja', 'ui.language_selected', language='日本語'), '表示言語：日本語')

    def test_catalogue_placeholder_parity(self):
        def fields(text):
            return {name for _, name, _, _ in Formatter().parse(text) if name is not None}
        self.assertEqual(set(i18n.CATALOGUES['en']), set(i18n.CATALOGUES['ja']))
        for key, japanese in i18n.CATALOGUES['ja'].items():
            self.assertEqual(fields(japanese), fields(i18n.CATALOGUES['en'][key]), key)

    def test_catalogues_not_reloaded_per_request(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('Unexpected file read')):
            for _ in range(3):
                self.assertEqual(i18n.translate(i18n.resolve_locale(request('ja')), 'nav.login'), 'ログイン')

    def test_core_has_no_service_dependencies(self):
        source = (ROOT/'app/i18n.py').read_text()
        imported = [n.module for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom)]
        self.assertFalse(any(name and name.startswith('app.') for name in imported))
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name.startswith(('app.db', 'app.security', 'app.services', 'torch', 'transformers')):
                raise AssertionError(name)
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=guarded_import):
            namespace = {'__file__': str(ROOT/'app/i18n.py')}
            exec(compile(source, 'isolated_i18n', 'exec'), namespace)
            self.assertEqual(namespace['resolve_locale'](request('ja')), 'ja')

    def test_safe_returns_keep_document_language_query(self):
        url = '/events/10/view-draft?lang=fr&search=hello%20world#document'
        self.assertEqual(i18n.safe_return_to(url), url)
        for value in ['', 'https://evil.example', '//evil.example/x', '/\\evil.example',
                      '/%2fevil.example', '/%255cevil.example', '/\r\nLocation:evil',
                      '/%0aevil', 'javascript:alert(1)', 'relative/path']:
            self.assertEqual(i18n.safe_return_to(value), '/', repr(value))


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.ns = isolated_functions()
        self.templates = Jinja2Templates(directory=str(ROOT/'app/templates'))
        i18n.install_jinja(self.templates.env)
        self.templates.env.filters['jst'] = self.ns['_format_jst']
        self.templates.env.globals.update(ai_features_enabled=False, getattr=getattr)
        self.templates.env.loader = ChoiceLoader([DictLoader({
            'test-page.html': '{% extends "base.html" %}{% block content %}{{ t("nav.login") }}{% endblock %}',
            'test-fragment.html': '{{ t("nav.login") }}|{{ ui_locale() }}',
            'test-macro.html': '{% macro label() %}{{ t("nav.back") }}{% endmacro %}',
            'test-import.html': '{% from "test-macro.html" import label with context %}{{ label() }}',
        }), self.templates.env.loader])
        self.app = FastAPI()
        self.app.middleware('http')(self.ns['ui_locale_context'])
        self.app.add_api_route('/ui-language', self.ns['set_ui_language'], methods=['POST'])

        @self.app.get('/')
        @self.app.get('/page')
        def page(request: Request):
            request.state.help_ctx = {}
            return self.templates.TemplateResponse(request, 'test-page.html', {
                'request': request, 'user': None, 'event': None, 'flags': {},
            })

        @self.app.get('/fragment')
        async def fragment(request: Request):
            await asyncio.sleep(0)  # Deliberately interleave requests.
            return {'html': self.templates.env.get_template('test-fragment.html').render(request=request)}
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_normal_response_html_language_default_and_japanese(self):
        for locale, label in [('en', 'Login'), ('ja', 'ログイン')]:
            response = self.client.get('/page', headers={'X-UI-Language': locale})
            self.assertEqual(response.status_code, 200)
            self.assertIn(f'<html lang="{locale}">', response.text)
            self.assertIn(label, response.text)
            self.assertIn('name="locale" value="ja"', response.text)

    def test_switch_cookie_and_navigation_persistence(self):
        response = self.client.post('/ui-language', data={'locale': 'ja', 'return_to': '/page?lang=fr'},
                                    follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/page?lang=fr')
        for path in ['/page', '/']:
            self.assertIn('<html lang="ja">', self.client.get(path).text)
        self.assertEqual(self.client.get('/fragment').json()['html'], 'ログイン|ja')
        cookie = response.headers['set-cookie'].lower()
        for attribute in ['ui_locale=ja', 'path=/', 'samesite=lax', 'httponly', 'max-age=31536000']:
            self.assertIn(attribute, cookie)
        self.assertNotIn('secure', cookie)
        self.assertNotIn('session=', cookie)

    def test_invalid_switch_locale_and_redirect(self):
        for value in ['invalid', 'ja-JP', '']:
            response = self.client.post('/ui-language', data={'locale': value, 'return_to': '//evil.example'},
                                        follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers['location'], '/')
            self.assertIn('ui_locale=en;', response.headers['set-cookie'])
        response = self.client.post('/ui-language', data={'locale': 'ja', 'return_to': 'https://evil.example'},
                                    follow_redirects=False)
        self.assertEqual(response.headers['location'], '/')

    def test_production_secure_cookie_uses_existing_convention(self):
        self.ns['UI_COOKIE_SECURE'] = True
        response = self.client.post('/ui-language', data={'locale': 'ja'}, follow_redirects=False)
        self.assertIn('Secure', response.headers['set-cookie'])
        self.assertIn('from app.security import IS_PROD as UI_COOKIE_SECURE', (ROOT/'app/main.py').read_text())

    def test_header_pins_fragments_independently_of_cookie(self):
        self.client.cookies.set('ui_locale', 'ja')
        self.assertEqual(self.client.get('/fragment', headers={'X-UI-Language': 'en'}).json()['html'], 'Login|en')
        self.assertEqual(self.client.get('/fragment').json()['html'], 'ログイン|ja')

    def test_concurrent_locales_do_not_leak(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
                locales = ['en', 'ja'] * 10
                responses = await asyncio.gather(*(client.get('/fragment', headers={'X-UI-Language': locale}) for locale in locales))
                for locale, response in zip(locales, responses):
                    self.assertEqual(response.json()['html'], 'ログイン|ja' if locale == 'ja' else 'Login|en')
        asyncio.run(run())

    def test_direct_fragments_macros_and_escaping(self):
        for locale, label in [('en', 'Back'), ('ja', '戻る')]:
            req = request(locale)
            req.state.ui_locale = i18n.resolve_locale(req)
            self.assertEqual(self.templates.env.get_template('test-import.html').render(request=req), label)
            html = self.templates.env.from_string('{{ t("ui.language_selected", language=value) }}').render(
                request=req, value='<script>unsafe</script>')
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)
        self.assertEqual(self.templates.env.get_template('test-fragment.html').render(), 'Login|en')

    def test_all_templates_parse_and_js_subset_is_small(self):
        for locale in ['en', 'ja']:
            for path in (ROOT/'app/templates').rglob('*.html'):
                self.templates.env.parse(path.read_text())
            html = self.templates.env.from_string('{{ ui_js_catalogue()|tojson }}').render(request=request(locale))
            self.assertEqual(set(json.loads(html)), set(i18n.JS_KEYS))

    def test_internal_values_and_user_content_are_untouched(self):
        template = self.templates.env.from_string(
            '<input name="choice" value="YES">{{ status }}|{{ role }}|{{ handle }}|{{ body }}')
        context = dict(status='ADOPTED', role='chairman', handle='@alex', body='Original user draft')
        self.assertEqual(template.render(request=request('en'), **context),
                         template.render(request=request('ja'), **context))

    def render_phase2(self, name, locale, **context):
        req = request(locale)
        req.state.help_ctx = {}
        self.templates.env.globals['url_for'] = lambda name, **params: '/static/' + params['path']
        values = dict(request=req, user=None, event=None, flags={}, dashboard_events=[])
        values.update(context)
        return self.templates.env.get_template(name).render(**values)

    def test_phase2_public_account_pages(self):
        examples = {
            'home.html': ('Download User Manual', '利用マニュアルをダウンロード'),
            'dashboard.html': ('No live or upcoming events', '開催中・開催予定のイベントはありません'),
            'login.html': ('Welcome back', 'おかえりなさい'),
            'register.html': ('Create your account', 'アカウントの作成'),
        }
        for name, labels in examples.items():
            for locale, label in zip(('en', 'ja'), labels):
                with self.subTest(template=name, locale=locale):
                    html = self.render_phase2(name, locale)
                    self.assertIn(label, html)
                    self.assertIn(f'<html lang="{locale}">', html)
                    self.assertIn('d¡alogüe.', html)
        for name in ['login.html', 'register.html']:
            html = self.render_phase2(name, 'ja')
            self.assertIn('パスワード', html)
            self.assertIn('name="password"', html)
            self.assertIn('method="post"', html)
        html = self.render_phase2('home.html', 'ja')
        self.assertIn('/static/manuals/user_manual_en.pdf', html)
        self.assertIn('/static/manuals/user_manual_ja.pdf', html)

    def test_phase2_navigation_and_role_presentation(self):
        user = SimpleNamespace(handle='alice<&>', roles=[])
        for locale, greeting, admin_label in [('en', 'hi,', 'Admin'), ('ja', 'こんにちは、', '管理画面')]:
            html = self.render_phase2('base.html', locale, user=user, flags={'IS_ADMIN': True})
            self.assertIn(greeting, html)
            self.assertIn(admin_label, html)
            self.assertIn('@alice&lt;&amp;&gt;', html)
            self.assertIn('href="/admin"', html)
        for role, image, ja_label in [('admin','admin','管理者'), ('member','member','参加者'),
                ('invited speaker','speaker','招待発言者'), ('president','president','president'),
                ('chairman','chairman','chairman')]:
            roles = [role]
            template = self.templates.env.from_string(
                '{% from "macros/user_flair.html" import flair_for_user with context %}'
                '{{ flair_for_user(u=user, roles_list=roles) }}')
            html = template.render(request=request('ja'), user=user, roles=roles)
            self.assertIn(f'title="{ja_label}"', html)
            self.assertIn(f'img/badges/{image}.png', html)
            self.assertEqual(roles, [role])

    def test_phase2_dashboard_keeps_authored_values_and_state(self):
        event = dict(id=41, menu_href='/events/41/menu', title='Original event <title>',
            starts_at_iso='2026-09-01T00:00:00Z', ends_at_iso='2026-09-01T01:00:00Z',
            starts_at_human='2026-09-01 09:00 JST', state='live', locked=True,
            stages=json.dumps([{'name':'General Floor'}]), stages_json=[{'name':'General Floor'}])
        for locale, label in [('en','Live'), ('ja','開催中')]:
            html = self.render_phase2('dashboard.html', locale, dashboard_events=[event])
            self.assertIn(label, html)
            self.assertIn('Original event &lt;title&gt;', html)
            self.assertIn('General Floor', html)
            self.assertIn('data-state="live"', html)
            self.assertIn('2026-09-01 09:00 JST', html)
            self.assertIn("setInterval(tickAll, 1000)", html)
            self.assertIn("}, 10000)", html)
        self.assertEqual(event['title'], 'Original event <title>')

    def test_phase2_dynamic_catalogue_and_placeholder_sentences(self):
        payload = json.loads(self.templates.env.from_string('{{ ui_js_catalogue()|tojson }}').render(request=request('ja')))
        self.assertEqual(payload['dashboard.ended_minutes'], '{count}分前に終了')
        self.assertEqual(payload['dashboard.countdown_hours'], '{hours}時間 {minutes}分 {seconds}秒')
        self.assertEqual(payload['common.ok'], 'OK')
        self.assertIn('notifications.floor_recognition', payload)
        self.assertIn("window.UII18n.t('common.ok')", (ROOT/'app/templates/base.html').read_text())
        for filename in ['login.html','register.html']:
            self.assertIn("t('account.password')", (ROOT/'app/templates'/filename).read_text())

    def test_phase2_catalogues_have_no_duplicate_or_unknown_template_keys(self):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                self.assertNotIn(key, result)
                result[key] = value
            return result
        for locale in ['en','ja']:
            json.loads((ROOT/f'app/locales/{locale}.json').read_text(), object_pairs_hook=unique)
        import re
        for name in ['base.html','home.html','dashboard.html','login.html','register.html','macros/user_flair.html','events_menu.html','events/propose_agenda.html',
                     'events/review_agenda.html','events/view_agenda.html','events/unlock_event.html',
                     'events/general_floor.html','events/general_floor_item.html','macros/interventions.html',
                     'partials/interventions_list.html','events/proposal_floor.html','events/proposal_floor_item.html',
                     'events/decision.html','partials/pfloor_vote_panel.html','macros/interventions-prop.html',
                     'partials/pfloor_interventions_list.html','proposal_discussion/index.html','rooms/index.html',
                     'rooms/show.html','partials/room_messages.html','partials/room_shared_state.html']:
            source = (ROOT/'app/templates'/name).read_text()
            for key in re.findall(r"\b(?:t|tr)\(['\"]([^'\"]+)", source):
                self.assertIn(key, i18n.CATALOGUES['en'], (name,key))

    def test_phase2_account_failures_keep_codes_and_uniform_login_errors(self):
        import re
        from unittest.mock import MagicMock
        from fastapi import HTTPException
        nodes = [n for n in ast.parse((ROOT/'app/main.py').read_text()).body
                 if isinstance(n, ast.FunctionDef) and n.name in ['post_login','post_register']]
        for node in nodes:
            node.decorator_list = []
        db = MagicMock()
        db.__enter__.return_value = db
        column = MagicMock()
        ns = dict(Request=Request, Form=Form, HTTPException=HTTPException, re=re,
                  request_locale=i18n.request_locale, translate=i18n.translate,
                  get_session=lambda: db, User=SimpleNamespace(email=column, handle=column),
                  select=lambda *args: SimpleNamespace(where=lambda *args: None),
                  verify_password=lambda *args: False, has_role=lambda *args: True)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'isolated_account_messages','exec'), ns)
        for locale in ['en','ja']:
            for user in [None, SimpleNamespace(password_hash=None), SimpleNamespace(password_hash='hash')]:
                db.exec.return_value.scalar_one_or_none.return_value = user
                with self.assertRaises(HTTPException) as raised:
                    ns['post_login'](request(locale), identifier='example', password='bad')
                self.assertEqual(raised.exception.status_code,400)
                self.assertEqual(raised.exception.detail,i18n.translate(locale,'account.invalid_credentials'))
            ns['verify_password'] = lambda *args: True
            db.exec.return_value.scalar_one_or_none.return_value = SimpleNamespace(password_hash='hash')
            with self.assertRaises(HTTPException) as raised:
                ns['post_login'](request(locale), identifier='example', password='good')
            self.assertEqual(raised.exception.status_code,403)
            self.assertEqual(raised.exception.detail,i18n.translate(locale,'account.banned'))
            ns['verify_password'] = lambda *args: False
            db.exec.return_value.first.return_value = object()
            with self.assertRaises(HTTPException) as raised:
                ns['post_register'](request(locale),handle='example',email='existing@example.com',password='bad')
            self.assertEqual(raised.exception.status_code,400)
            self.assertEqual(raised.exception.detail,i18n.translate(locale,'account.already_registered'))
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def phase3_context(self):
        # Extract the actual enum without importing models/DB or starting the app.
        from enum import Enum
        node = next(n for n in ast.parse((ROOT/'app/models.py').read_text()).body
                    if isinstance(n, ast.ClassDef) and n.name == 'ProposalStatus')
        ns = {'PyEnum': Enum}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'isolated_status', 'exec'), ns)
        proposal = SimpleNamespace(id=8, title='Authored agenda 日本語', background='Original background\n第二行',
            source_url='https://example.org/authored', created_at='2026-09-01T00:00:00Z',
            decided_at='2026-09-01T01:00:00Z', notes='Reviewer-authored note',
            proposer=SimpleNamespace(handle='author_handle'), status=ns['ProposalStatus'].accepted)
        event = dict(id=41, title='Authored event 日本語', starts_at_iso='2026-09-01T00:00:00Z',
            ends_at_iso='2026-09-02T00:00:00Z', stages_json=[{'name':'GENERAL_DEBATE',
            'kind':'debate', 'start':'2026-09-01T00:00:00Z', 'end':'2026-09-02T00:00:00Z'}])
        return dict(event=event, user=SimpleNamespace(handle='viewer', roles=[]),
                    mine=[proposal], pending=[proposal], decided=[proposal], proposals=[proposal])

    def test_phase3_pages_render_both_locales_and_keep_authored_content(self):
        examples = {'events_menu.html': ('Event Menu', 'イベントメニュー'),
            'events/propose_agenda.html': ('Agenda title', '議題のタイトル'),
            'events/review_agenda.html': ('Recently decided', '最近の審査結果'),
            'events/view_agenda.html': ('View Agenda', '議題を確認'),
            'events/unlock_event.html': ('Event passcode', 'イベントのパスコード')}
        context = self.phase3_context()
        for name, labels in examples.items():
            outputs = [self.render_phase2(name, locale, **context) for locale in ('en','ja')]
            for locale, label, html in zip(('en','ja'), labels, outputs):
                self.assertIn(label, html)
                self.assertIn(f'<html lang="{locale}">', html)
                self.assertIn(context['event']['title'].encode(), html.encode())
                if name in ['events/propose_agenda.html','events/review_agenda.html','events/view_agenda.html']:
                    for text in [context['mine'][0].title, context['mine'][0].background]:
                        self.assertIn(text.encode(), html.encode())
                    self.assertIn('2026-09-01 09:00 JST', html)
            if name == 'events/review_agenda.html':
                for html in outputs:
                    self.assertIn('Reviewer-authored note', html)
                    self.assertIn('author_handle', html)
                    for action in ['accept','reject','reopen']:
                        self.assertIn(f'value="{action}"', html)
                    self.assertIn('data-log-action="CLICK_ACCEPT_AGENDA"', html)
                self.assertIn('承認', outputs[1])
                self.assertIn('却下', outputs[1])
        self.assertEqual(context['mine'][0].status.value, 'accepted')
        self.assertEqual(context['event']['stages_json'][0]['name'], 'GENERAL_DEBATE')

    def test_phase3_status_enum_presentation_and_unknown_fallback(self):
        context = self.phase3_context()
        enum = type(context['mine'][0].status)
        for value, en, ja in [('pending','Pending','審査中'), ('accepted','Accepted','承認済み'),
                              ('rejected','Rejected','却下')]:
            for status in [value, enum(value)]:
                context['mine'][0].status = status
                for locale, label in [('en',en),('ja',ja)]:
                    html = self.render_phase2('events/propose_agenda.html',locale,**context)
                    self.assertIn(label,html)
                    self.assertNotIn('agenda.status.', html)
                self.assertEqual(context['mine'][0].status,status)
        context['mine'][0].status = 'future_status'
        self.assertIn('Future_status', self.render_phase2('events/propose_agenda.html','ja',**context))

    def test_phase3_menu_permissions_links_and_internal_stage_data(self):
        import re
        context = self.phase3_context()
        for flags, allowed in [({},False),({'IS_ADMIN':True},False),
                               ({'IS_PRESIDENT':True},True),({'IS_CHAIR':True},True)]:
            outputs = [self.render_phase2('events_menu.html',locale,flags=flags,**context) for locale in ('en','ja')]
            for html in outputs:
                self.assertEqual('data-key="review_agenda"' in html, allowed)
                self.assertIn('GENERAL_DEBATE',html)
                self.assertIn('General Floor',html)
                self.assertIn('Proposal Discussion',html)
                self.assertIn('setInterval(tick, 1000 * 15)',html)
            # Locale may change labels, never link destinations or gate identifiers.
            for pattern in [r'data-key="[^"]+"',r'href="/events/[^"]+"',r"data-stages='[^']*'"]:
                self.assertEqual(re.findall(pattern,outputs[0]),re.findall(pattern,outputs[1]))

    def test_phase3_known_errors_localize_without_translating_arbitrary_text(self):
        cases = [('events/propose_agenda.html','Missing required fields','必須項目を入力してください。'),
            ('events/propose_agenda.html','Invalid URL format','URLの形式を確認してください。'),
            ('events/review_agenda.html','Proposal not found','提案が見つかりません。'),
            ('events/unlock_event.html','Invalid passcode','パスコードが正しくありません。')]
        for name, en, ja in cases:
            for locale, label in [('en',en),('ja',ja)]:
                self.assertIn(label,self.render_phase2(name,locale,err=en,**self.phase3_context()))
            html = self.render_phase2(name,'ja',err='<script>custom</script>',**self.phase3_context())
            self.assertIn('&lt;script&gt;custom&lt;/script&gt;',html)

    def test_phase3_stage_catalogue_and_dynamic_sentences(self):
        # These pages have no stage-name panel. Pin approved display vocabulary
        # without adding UI or rewriting the raw stage data used by gating.
        for key,en,ja in [('opening','Opening','開会'),('general_debate','General Debate','一般討論'),
                          ('voting','Voting','投票')]:
            for locale,label in [('en',en),('ja',ja)]:
                html = self.templates.env.from_string('{{ t(key) }}').render(
                    request=request(locale),key='event.stage.'+key)
                self.assertEqual(html,label)
        payload = json.loads(self.templates.env.from_string('{{ ui_js_catalogue()|tojson }}').render(request=request('ja')))
        self.assertEqual(payload['event.locked'],'ロック中')
        self.assertEqual(payload['event.open'],'利用可能')
        self.assertEqual(i18n.translate('ja','event.opens_on',time='09:00 JST'),'09:00 JSTから利用できます')

    def general_floor_context(self, chair=False, opened=True):
        context = self.phase3_context()
        user = SimpleNamespace(id=2, handle='viewer', roles=[])
        item = dict(context['event'], proposal_id=8, id=60, title='Authored agenda 日本語',
                    background='Original background', source_url=None, created_at='2026-09-01T00:00:00Z', proposer=None)
        post = SimpleNamespace(id=1, by_user=2, body='Original speech 日本語',
            created_at='2026-09-01T00:00:00Z', relates_to_id=None)
        context.update(user=user, item=item, proposal=SimpleNamespace(id=8), q=SimpleNamespace(id=60),
            floor=SimpleNamespace(is_open=opened,current_speaker_request_id=1,speaking_time_sec=120),
            threads=[dict(node=post,children=[])], user_map={2:user}, role_map={2:['chairman' if chair else 'member']},
            flags=dict(IS_CHAIR=chair,IS_PRESIDENT=False,IS_ADMIN=False,IS_MEMBER=True),
            can_speak=chair,last_child_id=0,last_any_id=1,discussion_revision='1:1')
        context['initial_floor'] = dict(is_open=opened, can_speak=chair, can_manage=chair,
            current_req_id=None, current_user_id=None, current_kind=None,
            current_target_intervention_id=None, current_target_local_no=None, speakers=[])
        return context

    def test_phase4_general_floor_participant_and_chair_presentation(self):
        for locale, heading, speakers, request_label, ror in [
                ('en','General Floor','Speakers','Request the floor','Request Right of Reply'),
                ('ja','一般討論フロア','発言者リスト','発言を希望する','答弁権を申請')]:
            for chair in [False,True]:
                for opened in [False,True]:
                    ctx=self.general_floor_context(chair,opened)
                    html=self.render_phase2('events/general_floor_item.html',locale,**ctx)
                    for label in [heading,speakers,request_label,'Original speech 日本語','Authored agenda 日本語','@viewer']:
                        self.assertIn(label,html)
                    self.assertEqual('id="btn-call-next"' in html,chair)
                    self.assertEqual('value="ROR_ALL"' in html,chair)
                    self.assertIn('value="GENERAL"',html)
                    if chair:
                        self.assertIn('次の発言者の発言を許可' if locale=='ja' else 'Call next speaker',html)
                        expected=('発言者リストの受付を終了' if opened else '発言者リストの受付を開始') if locale=='ja' else ('Close list' if opened else 'Open list')
                        self.assertIn(expected,html)
                        self.assertIn('title="議長"' if locale=='ja' else 'title="chairman"',html)
                    else:
                        self.assertIn(ror,html)
                        self.assertIn('value="ROR"',html)
                    self.assertEqual(ctx['user'].id,2)
                    self.assertEqual(ctx['floor'].is_open,opened)
                    self.assertIn('AI機能は現在無効です' if locale=='ja' else 'AI features are currently disabled',html)
            index=self.render_phase2('events/general_floor.html',locale,**self.phase3_context())
            self.assertIn(heading,index)

    def test_phase4_direct_fragment_uses_pinned_locale_and_retains_content(self):
        self.templates.env.globals['url_for'] = lambda name, **params: '/static/' + params['path']
        ctx=self.general_floor_context()
        for locale, label in [('en','Request Right of Reply'),('ja','答弁権を申請')]:
            req=request(locale,'en' if locale=='ja' else 'ja')
            html=self.templates.env.get_template('partials/interventions_list.html').render(request=req,**ctx)
            self.assertIn(label,html)
            self.assertIn('Original speech 日本語',html)
            self.assertIn('value="ROR"',html)
            self.assertIn('data-discussion-revision="1:1"',html)
        ctx['threads']=[]
        self.assertIn('発言はまだありません。',self.templates.env.get_template('partials/interventions_list.html').render(request=request('ja'),**ctx))

    def test_phase4_permission_errors_keep_codes_and_do_not_touch_db(self):
        from fastapi import HTTPException
        from unittest.mock import Mock
        names={'floor_toggle_for_agenda':'toggle', 'floor_call_next_for_agenda':'call',
               'floor_finish_current_for_agenda':'finish', 'floor_take_now_for_agenda':'take',
               'floor_invite_ror_for_agenda':'invite'}
        nodes=[n for n in ast.parse((ROOT/'app/main.py').read_text()).body
               if isinstance(n,ast.FunctionDef) and n.name in names]
        for n in nodes:
            n.decorator_list=[]
            for arg in n.args.args: arg.annotation=None
            n.returns=None
        db=Mock(side_effect=AssertionError('Unauthorized action reached DB'))
        ns=dict(Form=Form,HTTPException=HTTPException,current_user=lambda req:SimpleNamespace(id=2),
                effective_flags=lambda u:dict(IS_PRESIDENT=False,IS_CHAIR=False,IS_ADMIN=False),
                get_session=db,translate=i18n.translate,request_locale=i18n.request_locale)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'isolated_floor_errors','exec'),ns)
        for locale in ['en','ja']:
            for name,key in names.items():
                with self.assertRaises(HTTPException) as raised:
                    ns[name](request=request(locale),event_id=10,pid=20)
                self.assertEqual(raised.exception.status_code,403)
                self.assertEqual(raised.exception.detail,i18n.translate(locale,'floor.error.'+key))
        db.assert_not_called()

    def test_phase4_dynamic_catalogue_and_scope(self):
        payload=json.loads(self.templates.env.from_string('{{ ui_js_catalogue()|tojson }}').render(request=request('ja')))
        for key,label in [('floor.queue.speaking','発言中'),('floor.queue.queued','待機中'),
                          ('floor.queue.ror','答弁権'),('floor.ror.all','討論全体への答弁権')]:
            self.assertEqual(payload[key],label)
        self.assertEqual(i18n.translate('ja','floor.ror.invited_target',target=17),'議長から発言#17への答弁を依頼されました。')
        source=(ROOT/'app/static/js/deliberation-polling.js').read_text()
        self.assertIn('(options.generalFloor || options.proposalFloor) ? window.UII18n.t(key, params) : fallback',source)
        self.assertIn('generalFloor: true',(ROOT/'app/templates/events/general_floor_item.html').read_text())
        self.assertNotIn('generalFloor: true',(ROOT/'app/templates/events/proposal_floor_item.html').read_text())
        self.assertEqual(i18n.translate('ja','roles.chairman'),'chairman')

    def proposal_floor_context(self, chair=False):
        ctx=self.general_floor_context(chair)
        draft=SimpleNamespace(id=40,status='TABLED',title='Authored proposal 日本語',l_number='L.1',
            sponsor_id=2,cosigners_json=[],recalling='Authored clause 日本語')
        vote=SimpleNamespace(is_open=True,yes=2,no=1,abstain=7)
        ctx.update(event_id=41,kind='draft',item_id=40,mode='DRAFT',draft=draft,amendment=None,amendment_cards=[],
            pfloor=SimpleNamespace(is_open=True,event_id=41,proposal_id=8),
            early_vote=vote,formal_vote=SimpleNamespace(is_open=True,yes=2,no=1,abstain=7),
            HAS_EARLY_VOTED=False,HAS_FORMAL_VOTED=False,can_speak=chair,
            rows=[dict(draft=draft,proposal=SimpleNamespace(title='Authored agenda 日本語'),amendments=[])])
        return ctx

    def test_phase5_pages_and_ballot_values_in_both_locales(self):
        import re
        for chair in [False,True]:
            ctx=self.proposal_floor_context(chair)
            for locale,heading in [('en','Proposal Floor'),('ja','提案審議フロア')]:
                for name in ['events/proposal_floor.html','events/proposal_floor_item.html']:
                    html=self.render_phase2(name,locale,**ctx)
                    self.assertIn(heading,html)
                    self.assertIn('Authored proposal 日本語',html)
                html=self.render_phase2('events/proposal_floor_item.html',locale,**ctx)
                self.assertIn('Authored clause 日本語',html)
                self.assertIn('単純過半数' if locale=='ja' else 'Simple majority',html)
                self.assertEqual('/early/close"' in html,chair)
                self.assertEqual('/formal/close"' in html,chair)
                forms=re.findall(r'<form[^>]*>.*?</form>',html,re.S)
                for value,en,ja in [('YES','Yes','賛成'),('NO','No','反対'),('ABSTAIN','Abstain','棄権')]:
                    ballots=[f for f in forms if f'name="choice" value="{value}"' in f]
                    self.assertEqual(len(ballots),2)
                    self.assertTrue(all((ja if locale=='ja' else en) in f for f in ballots))
                    self.assertNotIn(f'name="choice" value="{ja}"',html)
            self.assertEqual(ctx['draft'].status,'TABLED')
            self.assertEqual(ctx['early_vote'].abstain,7)

    def test_phase5_status_and_result_mapping_does_not_mutate_raw_values(self):
        from enum import Enum
        template=self.templates.env.from_string(
            "{% from 'macros/vote_labels.html' import status_label,result_label with context %}"
            "{{ status_label(status) }}|{{ result_label(result) }}")
        class Status(str,Enum):
            TABLED='TABLED';ADOPTED='ADOPTED';WITHDRAWN='WITHDRAWN';REINTRODUCED='REINTRODUCED'
        for raw,label in [('TABLED','上程済み'),('ADOPTED','採択'),('WITHDRAWN','撤回'),('REINTRODUCED','再提出'),('REJECTED','否決')]:
            for value in [raw]+([Status(raw)] if raw!='REJECTED' else []):
                html=template.render(request=request('ja'),status=value,result='Rejected')
                self.assertEqual(html,label+'|否決')
                self.assertEqual(value,raw)
        self.assertEqual(template.render(request=request('ja'),status='UNKNOWN',result='<script>new</script>'),
                         'UNKNOWN|&lt;script&gt;new&lt;/script&gt;')
        self.assertEqual(template.render(request=request('en'),status='ADOPTED',result='Adopted'),'ADOPTED|Adopted')

    def test_phase5_amendment_result_classification_uses_original_english(self):
        ctx=self.proposal_floor_context()
        am=SimpleNamespace(id=50,label='Authored amendment',am_no=1,body_markdown='Original amendment text')
        for result,label,color in [('Adopted','採択','text-emerald-700'),('Rejected','否決','text-rose-700'),
                                  ('Adopted by consensus','合意により採択','text-emerald-700'),
                                  ('Voting open','投票受付中','text-blue-700'),('No consensus','合意に至りませんでした','text-slate-600')]:
            summary=dict(result=result,kind='Formal vote',exists=True,is_open=False,yes=2,no=1,abstain=9)
            ctx['amendment_cards']=[dict(am=am,vote_summary=summary)]
            html=self.render_phase2('partials/pfloor_vote_panel.html','ja',**ctx)
            self.assertIn(label,html)
            import re
            self.assertRegex(html,rf'<span class="{color}[^"]*">{label}</span>')
            self.assertEqual(summary['result'],result)
            self.assertIn('Original amendment text',html)

    def test_phase5_decision_page_preserves_outcomes_and_authored_titles(self):
        ctx=self.proposal_floor_context()
        ctx['draft'].status=SimpleNamespace(value='ADOPTED')
        item=dict(draft=ctx['draft'],outcome='Adopted',early_vote=ctx['early_vote'],formal_vote=ctx['formal_vote'])
        for locale,label in [('en','Adopted'),('ja','採択')]:
            html=self.render_phase2('events/decision.html',locale,results_drafts=[item],results_amendments=[],**ctx)
            self.assertIn(label,html)
            self.assertIn('Authored proposal 日本語',html)
            self.assertIn('bg-emerald-100 text-emerald-800',html)
        self.assertEqual(item['outcome'],'Adopted')
        self.assertEqual(ctx['draft'].status.value,'ADOPTED')

    def test_phase5_vote_actions_reject_japanese_values_and_keep_permissions(self):
        from fastapi import HTTPException
        from unittest.mock import Mock
        names=['pfloor_early_vote','pfloor_formal_vote','pfloor_early_open','pfloor_early_close',
               'pfloor_formal_open','pfloor_formal_close','pfloor_close_discussion']
        nodes=[n for n in ast.parse((ROOT/'app/main.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name in names]
        for n in nodes:n.decorator_list=[]
        db=Mock(side_effect=AssertionError('Invalid/unauthorized vote accessed database'))
        ns=dict(Request=Request,Form=Form,HTTPException=HTTPException,get_session=db,
                current_user=lambda req:SimpleNamespace(id=2),effective_flags=lambda u:dict(IS_CHAIR=False,IS_PRESIDENT=False),
                translate=i18n.translate,request_locale=i18n.request_locale)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'isolated_vote_guards','exec'),ns)
        for locale in ['en','ja']:
            for name in names:
                if name.endswith('_vote'):
                    for choice in ['賛成','反対','棄権']:
                        with self.assertRaises(HTTPException) as raised:
                            ns[name](kind='draft',id=40,event_id=41,request=request(locale),choice=choice)
                        self.assertEqual(raised.exception.status_code,400)
                else:
                    with self.assertRaises(HTTPException) as raised:
                        ns[name](kind='draft',id=40,event_id=41,request=request(locale))
                    self.assertEqual(raised.exception.status_code,403)
        db.assert_not_called()

    def room_context(self, sponsor=False, submitted=False):
        from datetime import datetime, timezone
        people = {uid: SimpleNamespace(id=uid, handle=handle, roles=[])
                  for uid, handle in [(1, 'Sponsor_日本語'), (2, 'Viewer_EN')]}
        fields = dict.fromkeys(['recalling', 'noting', 'welcoming', 'expressing_regret',
            'expressing_deep_concern', 'emphasizing', 'decides', 'requests', 'calls_upon', 'encourages'], '')
        fields.update(recalling='Authored background 日本語 <unchanged>',
                      decides='Calls upon all participants to... & 保持')
        draft = SimpleNamespace(id=40, title='User draft 日本語', is_submitted=submitted,
            status='TABLED', l_number='L.1', submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc), **fields)
        proposal = SimpleNamespace(id=20, title='Authored agenda 日本語')
        room = SimpleNamespace(id=30, proposal_id=20, title='Authored room 日本語', sponsor_id=1, sponsor=people[1])
        message = SimpleNamespace(id=50, user_id=1, body='Chat 日本語 <literal> & content',
                                  created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        return dict(event=SimpleNamespace(id=10, title='Authored event 日本語'),
            proposal=proposal, proposals=[proposal], room_counts={20: 1}, rooms=[room], room=room,
            user=people[1 if sponsor else 2], user_map=people, draft=draft, cos_list=[],
            threads=[SimpleNamespace(node=message, children=[])], flags={})

    def render_room(self, name, locale, **context):
        req = request(locale)
        req.state.help_ctx = {}
        def url_for(name, **params):
            if name == 'static':
                return '/static/' + params['path']
            base = f"/events/{params['event_id']}/proposal-discussion/{params['pid']}/rooms"
            if name == 'rooms_create':
                return base
            return base + f"/{params['rid']}" + ('/delete' if name == 'rooms_delete' else '')
        self.templates.env.globals['url_for'] = url_for
        return self.templates.env.get_template(name).render(request=req, **context)

    def test_phase6_room_indexes_and_authored_titles(self):
        ctx = self.room_context()
        for locale, heading, create in [('en', 'Proposal Discussion', 'Create room'),
                                        ('ja', '提案討議', 'ルームを作成')]:
            index = self.render_room('proposal_discussion/index.html', locale, **ctx)
            rooms = self.render_room('rooms/index.html', locale, **ctx)
            self.assertIn(heading, index)
            self.assertIn(create, rooms)
            for value in [ctx['proposal'].title, ctx['room'].title, ctx['room'].sponsor.handle]:
                self.assertIn(value, rooms)
            self.assertNotIn('/30/delete', rooms)
            chair = dict(ctx, flags={'IS_CHAIRMAN': True})
            self.assertIn('/30/delete', self.render_room('rooms/index.html', locale, **chair))

    def test_phase6_editor_labels_inputs_and_permissions(self):
        import re
        from html import unescape
        ctx = self.room_context(sponsor=True)
        rendered = []
        for locale, labels in [('en', ['Proposal Draft', 'Preambular clauses', 'Operative clauses', 'Save Draft', 'Send']),
                ('ja', ['提案草案', '前文条項', '主文条項', '草案を保存', '送信', '編集ワークスペース', '編集中'])]:
            html = self.render_room('rooms/show.html', locale, ai_features_enabled=False, **ctx)
            rendered.append(html)
            for label in labels:
                self.assertIn(label, html)
            for field in ['recalling', 'decides']:
                content = re.search(r'<textarea id="draft_' + field + r'"[^>]*>(.*?)</textarea>', html, re.S)[1]
                self.assertEqual(unescape(content), getattr(ctx['draft'], field))
            self.assertIn('name="parent_id" id="parent_id" value=""', html)
            self.assertIn('name="body"', html)
            self.assertIn('/draft/save', html)
            self.assertIn('/draft/submit', html)
            self.assertNotIn('/draft/cosign"', html)
        # Form destinations, field names and logging identifiers remain locale-independent.
        for pattern in [r'\baction="([^"]+)"', r'\bname="([^"]+)"', r'data-log-action="([^"]+)"']:
            self.assertEqual(re.findall(pattern, rendered[0]), re.findall(pattern, rendered[1]))
        self.assertEqual(ctx['draft'].status, 'TABLED')
        member = self.render_room('rooms/show.html', 'ja', **self.room_context())
        self.assertNotIn('id="draft_title"', member)
        self.assertIn('閲覧専用', member)
        self.assertIn('この草案に共同署名する', member)
        submitted = self.render_room('rooms/show.html', 'ja', **self.room_context(sponsor=True, submitted=True))
        self.assertNotIn('id="draft_title"', submitted)
        self.assertIn('提出済み', submitted)
        self.assertNotIn('/draft/cosign"', submitted)

    def test_phase6_shared_guidance_used_unused_cosign_and_content(self):
        import re
        from html import unescape
        ctx = self.room_context()
        outputs = [self.render_room('partials/room_shared_state.html', locale, **ctx) for locale in ('en','ja')]
        for html in outputs:
            values = [unescape(v) for v in re.findall(r'<div class="draft-clause-value">(.*?)</div>', html, re.S)]
            self.assertEqual(values, [ctx['draft'].recalling, ctx['draft'].decides])
            self.assertEqual(html.count('data-draft-hint='), 10)
        for label in ['保存済み作業草案', '提案者', '共同署名者', '未使用', '前文条項', '主文条項']:
            self.assertIn(label, outputs[1])
        for clause in ['recalling', 'noting', 'decides', 'encourages']:
            self.assertIn(i18n.translate('ja', 'draft.guidance.'+clause), outputs[1])
        cosigned = self.render_room('partials/room_shared_state.html', 'ja', **dict(ctx, cos_list=[2]))
        self.assertIn('共同署名を取り消す', cosigned)
        self.assertIn('/events/10/proposal-discussion/20/rooms/30/draft/cosign', cosigned)
        self.assertEqual(ctx['cos_list'], [])

    def test_phase6_chat_content_and_role_presentation(self):
        import re
        from html import unescape
        ctx = self.room_context()
        ctx['user_map'][1].roles = [SimpleNamespace(name='chairman')]
        for locale, role in [('en', 'chairman'), ('ja', '議長')]:
            html = self.render_room('partials/room_messages.html', locale, **ctx)
            self.assertIn(f'alt="{role}"', html)
            body = re.search(r'<div class="room-message-bubble">(.*?)</div>', html, re.S)[1]
            self.assertEqual(unescape(body), ctx['threads'][0].node.body)
            self.assertIn('@Sponsor_日本語', html)
            self.assertIn('data-post-id="50"', html)
            self.assertIn('9:00 AM', html)  # Existing JST formatting.
        self.assertEqual(ctx['user_map'][1].roles[0].name, 'chairman')
        empty = self.render_room('partials/room_messages.html', 'ja', **dict(ctx, threads=[]))
        self.assertIn('討議を始めましょう', empty)

    def test_phase6_ai_presentation_and_page_catalogue(self):
        import re
        ctx = self.room_context(sponsor=True)
        for locale, label in [('en', 'Generate draft from paragraphs'), ('ja', '文章から草案を生成')]:
            html = self.render_room('rooms/show.html', locale, ai_features_enabled=True, **ctx)
            button = re.search(r'<button[^>]*id="btn_generate"[^>]*>(.*?)</button>', html, re.S)
            self.assertIn(label, button[1])
            self.assertNotIn('disabled', button[0])
            self.assertIn('name="recalling"', html)
            self.assertIn('fd.append("plain_text", plain)', html)
        disabled = self.render_room('rooms/show.html', 'ja', ai_features_enabled=False, **ctx)
        self.assertRegex(disabled, r'id="btn_generate"\s+disabled')
        self.assertIn('手動での起草を続けられます', disabled)
        payload = json.loads(self.templates.env.from_string('{{ ui_js_catalogue()|tojson }}').render(request=request('ja')))
        self.assertEqual(payload['draft.ai.elapsed'], '生成中… {seconds}秒')
        self.assertEqual(payload['draft.ai.done'], '草案を生成しました。内容を確認してから保存してください。')
        source = (ROOT/'app/templates/rooms/show.html').read_text()
        self.assertIn('error: message', source)
        self.assertIn('throw new Error(data.error || "Draft generation failed.")', source)
        self.assertIn("window.UII18n.t('draft.ai.failed'", source)
        self.assertNotIn('草案', source)  # Japanese strings are only in the catalogue.

    def test_phase6_manual_validation_keeps_status_codes_and_has_no_writes(self):
        from fastapi import HTTPException
        from unittest.mock import MagicMock
        import copy
        tree = ast.parse((ROOT/'app/main.py').read_text())
        names = {'rooms_create', 'room_post_message', 'draft_submit', 'draft_save', 'draft_cosign'}
        nodes = [copy.deepcopy(n) for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        for node in nodes:
            node.decorator_list = []
        ctx = self.room_context(sponsor=True)
        draft, room = ctx['draft'], ctx['room']
        db = MagicMock()
        db.__enter__.return_value = db
        db.get.return_value = room
        db.exec.return_value.first.return_value = draft
        forbidden = AssertionError('Unexpected persistence or AI')
        for action in ['add', 'commit', 'refresh']:
            getattr(db, action).side_effect = forbidden
        ns = dict(Request=Request, Form=Form, HTTPException=HTTPException, RedirectResponse=RedirectResponse,
            current_user=lambda req: ctx['user'], get_session=lambda: db,
            ProposalDraft=MagicMock(), ProposalRoom=MagicMock(), select=MagicMock(),
            _require_event_access=lambda **kw: None, _get_event_room_or_404=lambda *a: room,
            request_locale=i18n.request_locale, translate=i18n.translate)
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]+nodes, type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), 'isolated_phase6_validation', 'exec'), ns)
        for locale in ['en', 'ja']:
            for name, kwargs, key in [
                ('rooms_create', dict(title=' '), 'proposal_discussion.error.title'),
                ('room_post_message', dict(body=' ', room_id=30), 'proposal_discussion.error.message')]:
                with self.assertRaises(HTTPException) as raised:
                    ns[name](event_id=10, pid=20, request=request(locale), **kwargs)
                self.assertEqual(raised.exception.status_code, 400)
                self.assertEqual(raised.exception.detail, i18n.translate(locale, key))
            for title, recalling, decides, key in [('', '', '', 'title'), ('Title', '', '', 'preambular'),
                                                  ('Title', 'Author input', '', 'operative')]:
                draft.title, draft.recalling, draft.decides = title, recalling, decides
                with self.assertRaises(HTTPException) as raised:
                    ns['draft_submit'](event_id=10, pid=20, rid=30, request=request(locale))
                self.assertEqual(raised.exception.status_code, 400)
                self.assertEqual(raised.exception.detail, i18n.translate(locale, 'draft.error.'+key))
            ctx['user'] = ctx['user_map'][2]
            with self.assertRaises(HTTPException) as raised:
                ns['draft_save'](event_id=10, pid=20, rid=30, request=request(locale))
            self.assertEqual(raised.exception.status_code, 403)
            ctx['user'] = ctx['user_map'][1]
            draft.is_submitted = True
            with self.assertRaises(HTTPException) as raised:
                ns['draft_cosign'](event_id=10, pid=20, rid=30, request=request(locale))
            self.assertEqual(raised.exception.status_code, 400)
            draft.is_submitted = False
        self.assertEqual(draft.status, 'TABLED')

    def test_unsaved_guard_excludes_polling_hidden_fields(self):
        # Source-level guard, like the polling-header test below. A hidden
        # reply ID is server state, not evidence of an unfinished user edit.
        source = (ROOT/'app/static/js/ui-i18n.js').read_text()
        guard = "if (el.type === 'hidden') return false;"
        self.assertIn(guard, source)
        self.assertLess(source.index(guard), source.index('el.cloneNode(true)'))
        self.assertNotIn("['parent_id', 'relates_to_id']", source)

    def test_shared_poller_change_is_only_locale_header(self):
        source = (ROOT/'app/static/js/deliberation-polling.js').read_text()
        self.assertIn("const uiLocale = document.documentElement.lang === 'ja' ? 'ja' : 'en'", source)
        self.assertIn("headers: {'X-UI-Language': uiLocale}", source)
        self.assertIn('const interval = 4000', source)
        self.assertIn('version === epoch', source)


if __name__ == '__main__':
    unittest.main()
