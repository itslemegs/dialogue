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
    names = {'ui_locale_context', 'set_ui_language'}
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
        for name in ['base.html','home.html','dashboard.html','login.html','register.html','macros/user_flair.html']:
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
