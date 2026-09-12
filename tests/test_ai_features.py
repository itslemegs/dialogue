"""Isolated Phase 1 regressions; never import main/db or run app startup.

Run with: python -m unittest discover -s tests -v
Requires FastAPI, httpx, Jinja2 and python-multipart. Handlers are compiled from
source because importing main performs database/model setup. The test ASGI app
has no lifespan; all database, executor, network and model work is mocked.
"""
import ast
import asyncio
import builtins
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader

from app import ai_features as policy

ROOT = Path(__file__).resolve().parents[1]
MAIN = 'app/main.py'


def source_function(path, name, **extra):
    """Compile the actual final definition, skipping module import side effects."""
    tree = ast.parse((ROOT / path).read_text())
    node = [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name][-1]
    node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = dict(require_ai_features=policy.require_ai_features,
              AI_FEATURES_ENABLED=policy.AI_FEATURES_ENABLED,
              AIFeaturesDisabled=policy.AIFeaturesDisabled,
              Request=Request, Form=Form, HTTPException=HTTPException,
              JSONResponse=JSONResponse, RedirectResponse=RedirectResponse)
    ns.update(extra)
    exec(compile(module, str(ROOT / path), 'exec'), ns)
    return ns[name]


def load_translation():
    spec = importlib.util.spec_from_file_location('isolated_translation', ROOT / 'app/services/local_translate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FlagTests(unittest.TestCase):
    def test_flag_values_and_process_snapshot(self):
        for value, enabled in [(None, False), ('false', False), ('true', True),
                               (' TRUE ', True), ('invalid', False), ('1', False), ('', False)]:
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                if value is not None:
                    os.environ['AI_FEATURES_ENABLED'] = value
                spec = importlib.util.spec_from_file_location('isolated_policy', ROOT / 'app/ai_features.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertEqual(module.AI_FEATURES_ENABLED, enabled)
                os.environ['AI_FEATURES_ENABLED'] = 'false' if enabled else 'true'
                self.assertEqual(module.AI_FEATURES_ENABLED, enabled)


class DisabledTests(unittest.TestCase):
    def setUp(self):
        self.flag = patch.object(policy, 'AI_FEATURES_ENABLED', False)
        self.flag.start()
        self.addCleanup(self.flag.stop)

    def test_translation_import_is_light_and_loaders_are_blocked(self):
        real_import = builtins.__import__
        def safe_import(name, *args, **kwargs):
            if name.split('.')[0] in ('torch', 'transformers'):
                self.fail('Disabled translation imported ' + name)
            return real_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=safe_import):
            translate = load_translation()
            loader = Mock()
            with patch.object(translate, '_initialize_runtime', loader):
                for fn, args in [(translate.warm_model, ('fr',)),
                                 (translate._load_bundle, ('fr',)),
                                 (translate.translate_many_en_to, (['Hello'], 'fr'))]:
                    with self.assertRaises(policy.AIFeaturesDisabled):
                        fn(*args)
            loader.assert_not_called()
            self.assertIsNone(translate.torch)

    def test_service_guards_precede_fallbacks_network_and_database(self):
        # No other globals are provided: any preprocessing, fallback, DB access,
        # model request or queue work before the guard makes these calls fail.
        cases = [
            ('app/services/draft_ai.py', 'generate_draft_from_paragraphs', dict(plain_text='Draft')),
            ('app/services/amend_ai.py', 'generate_amend_ops_from_paragraphs', dict(plain_text='Amend')),
            ('app/services/local_llm.py', 'get_chat_client', {}),
            ('app/services/local_ollama.py', 'get_chat_client', {}),
            ('app/services/draft_translation.py', 'get_cached_or_enqueue_draft_translation', dict(db=Mock(), draft=Mock(), lang='fr', fields=['decides'])),
            ('app/services/draft_translation.py', '_enqueue', dict(job=Mock())),
            ('app/services/draft_translation.py', '_run_job', dict(job=Mock())),
            ('app/services/live_summary.py', 'refresh_summary_if_needed', dict(db=Mock(), scope=Mock())),
            ('app/services/live_summary.py', 'ollama_generate', dict(prompt='Hello')),
            ('app/services/lookup_ai.py', 'run_lookup', dict(req=Mock())),
        ]
        for path, name, kwargs in cases:
            with self.subTest(name=name, path=path), self.assertRaises(policy.AIFeaturesDisabled):
                result = source_function(path, name)(**kwargs)
                if asyncio.iscoroutine(result):
                    asyncio.run(result)
        for path in ['app/services/local_llm.py', 'app/services/local_ollama.py']:
            client = Mock()
            with self.assertRaises(policy.AIFeaturesDisabled):
                source_function(path, 'chat_completion')(client, messages=[])
            self.assertEqual(client.mock_calls, [])

    def test_disabled_endpoints_are_terminal_without_jobs_or_database(self):
        app = FastAPI()
        app.add_exception_handler(policy.AIFeaturesDisabled, source_function(MAIN, 'ai_disabled_response'))
        executor = Mock()
        db = Mock(side_effect=AssertionError('Unexpected database access'))
        base = '/events/1/proposal-discussion/2/rooms/3'
        routes = [
            (MAIN, 'draft_generate', 'POST', '/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/draft/generate', base+'/draft/generate'),
            (MAIN, 'draft_generate_json', 'POST', '/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/draft/generate-json', base+'/draft/generate-json'),
            (MAIN, 'amend_generate_json', 'POST', '/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/drafts/{draft_id}/amend/generate-json', base+'/drafts/4/amend/generate-json'),
            (MAIN, 'draft_generate_status', 'GET', '/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/draft/generate/status/{job_id}', base+'/draft/generate/status/test'),
            (MAIN, 'draft_generate_json_status', 'GET', '/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/draft/generate-json/status/{job_id}', base+'/draft/generate-json/status/test'),
            (MAIN, 'amend_generate_json_status', 'GET', '/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/drafts/{draft_id}/amend/generate-json/status/{job_id}', base+'/drafts/4/amend/generate-json/status/test'),
            ('app/routes/live_summary_fragment.py', 'api_live_summary', 'GET', '/api/live_summary', '/api/live_summary?kind=GENERAL&question_id=1'),
            ('app/routers/ai_lookup.py', 'ai_lookup', 'POST', '/ai/lookup', '/ai/lookup'),
        ]
        for path, name, method, route, url in routes:
            app.add_api_route(route, source_function(path, name, get_session=db, DRAFT_GENERATION_EXECUTOR=executor), methods=[method])
        app.add_api_route('/drafts/{draft_id}/translation-status', source_function(MAIN, 'draft_translation_status', get_session=db))
        client = TestClient(app)
        for _, name, method, _, url in routes:
            with self.subTest(endpoint=name):
                response = client.request(method, url, data={'plain_text': 'Please draft this'} if method == 'POST' else None)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()['status'], 'disabled')
        response = client.get('/drafts/4/translation-status?lang=fr')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'disabled')
        self.assertFalse(response.json()['ready'])
        executor.submit.assert_not_called()
        db.assert_not_called()

    def test_translated_document_views_show_original(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        draft = SimpleNamespace(id=4, title='Original title', decides='Original clause',
            sponsor_id=1, submitted_at=now-timedelta(days=1), cosigners_json=[], event_id=1)
        amend = SimpleNamespace(id=5, draft_id=4, body_markdown='Original amendment')
        db = MagicMock()
        db.get.side_effect = lambda model, ident: {4: draft, 5: amend}.get(ident, SimpleNamespace(handle='Sponsor'))
        db.exec.return_value.all.return_value = []
        session = MagicMock()
        session.return_value.__enter__.return_value = db
        translation = Mock(side_effect=AssertionError('Unexpected translation'))
        render = Mock(side_effect=lambda name, request, **context: context)
        shared = dict(current_user=lambda request: None, get_session=session,
            ProposalDraft=object(), User=object(), Amendment=MagicMock(), select=MagicMock(),
            SUPPORTED={'en': {'dir': 'ltr'}, 'fr': {'dir': 'ltr'}},
            translate_lang_meta=lambda lang: {'dir': 'ltr'}, render=render,
            translate_text=translation, get_cached_or_enqueue_draft_translation=translation)
        request = SimpleNamespace(query_params={'lang': 'fr'})
        view = source_function(MAIN, 'draft_detail', **shared,
            normalize_translation_lang=lambda lang, supported: lang, _now_utc=lambda: now,
            VISIBLE_AFTER=timedelta(0), _agenda_for_draft=lambda db,d: (2,'Agenda'),
            FIELDS=['decides'], DONE='DONE', labels_for=lambda lang: {},)
        result = view(4, request)
        self.assertEqual(result['draft_text'], {'decides': 'Original clause'})
        self.assertEqual(result['title_show'], 'Original title')
        self.assertFalse(result['hide_draft_for_translation'])
        self.assertTrue(result['translation_disabled'])
        self.assertEqual(result['lang'], 'en')
        shared['current_user'] = lambda request: SimpleNamespace(id=1)
        view = source_function(MAIN, 'view_amendment', **shared,
            _assert_amend_context=lambda **kwargs: (SimpleNamespace(), draft, SimpleNamespace()))
        result = view(1,2,3,4,5,request)
        self.assertEqual(result['body_show'], 'Original amendment')
        self.assertTrue(result['translation_disabled'])
        self.assertEqual(result['lang'], 'en')
        self.assertEqual(list(result['supported_langs']), ['en'])
        translation.assert_not_called()


class DispatchTests(unittest.TestCase):
    def test_startup_only_warms_when_enabled(self):
        for enabled in [False, True]:
            with self.subTest(enabled=enabled):
                http = Mock()
                engine = MagicMock()
                engine.begin.return_value.__aenter__.return_value.run_sync = AsyncMock()
                lifespan = source_function(MAIN, 'lifespan', AI_FEATURES_ENABLED=enabled,
                    init_db=Mock(), engine=engine, log=Mock(), os=os, httpx=http)
                async def exercise():
                    gen = lifespan(None)
                    await anext(gen)
                    await gen.aclose()
                asyncio.run(exercise())
                self.assertEqual(http.post.call_count, int(enabled))
                # Execute the real module-level warmup conditional in isolation.
                tree = ast.parse((ROOT/MAIN).read_text())
                warm_if = next(n for n in tree.body if isinstance(n, ast.If)
                    and isinstance(n.test, ast.Name) and n.test.id == 'AI_FEATURES_ENABLED')
                warm = Mock()
                exec(compile(ast.Module(body=[warm_if], type_ignores=[]), MAIN, 'exec'),
                     dict(AI_FEATURES_ENABLED=enabled, warm_model=warm))
                self.assertEqual([c.args[0] for c in warm.call_args_list],
                                 ['ar','zh','fr','ru','es'] if enabled else [])

    def test_enabled_generation_worker_calls_existing_generator(self):
        with patch.object(policy, 'AI_FEATURES_ENABLED', True):
            fill = Mock()
            fill.model_dump.return_value = {'title': 'Generated'}
            generate, finish, set_job = Mock(return_value=fill), Mock(), Mock()
            worker = source_function(MAIN, '_run_draft_generation_job',
                generate_draft_from_paragraphs=generate, _finish_draft_generation=finish,
                _set_draft_job=set_job)
            worker(job_id='job', plain_text='Notes', agenda_title='Agenda', room_title='Room')
            generate.assert_called_once_with(plain_text='Notes', agenda_title='Agenda', room_title='Room')
            self.assertEqual(set_job.call_args.args[1]['status'], 'done')
            finish.assert_called_once()

    def test_enabled_translation_uses_existing_loader_and_cache(self):
        with patch.object(policy, 'AI_FEATURES_ENABLED', True):
            translate = load_translation()
            bundle = (Mock(), Mock())
            with patch.object(translate, '_load_bundle', return_value=bundle) as loader:
                translate.warm_model('fr')
                self.assertEqual(translate._get_bundle('fr'), bundle)
                loader.assert_called_once_with('fr')

    def test_enabled_draft_endpoint_submits_existing_job(self):
        with patch.object(policy, 'AI_FEATURES_ENABLED', True):
            db, session = MagicMock(), MagicMock()
            session.return_value.__enter__.return_value = db
            db.get.side_effect = [SimpleNamespace(proposal_id=2, event_id=1, sponsor_id=9, title='Room'),
                                  SimpleNamespace(title='Agenda')]
            db.exec.return_value.first.return_value = None
            executor, worker = Mock(), Mock()
            route = source_function(MAIN, 'draft_generate_json',
                current_user=lambda request: SimpleNamespace(id=9), get_session=session,
                ProposalRoom=Mock(), ProposalDraft=MagicMock(), AgendaProposal=Mock(),
                select=MagicMock(), uuid=SimpleNamespace(uuid4=lambda: SimpleNamespace(hex='job')),
                _set_draft_job=Mock(), _try_start_draft_generation=lambda: True,
                DRAFT_GENERATION_EXECUTOR=executor, _run_draft_generation_job=worker)
            response = route(pid=2, rid=3, request=Mock(), event_id=1, plain_text='Notes')
            self.assertEqual(response.status_code, 200)
            executor.submit.assert_called_once_with(worker, job_id='job', plain_text='Notes',
                                                      agenda_title='Agenda', room_title='Room')

    def test_enabled_amendment_worker_dispatches_and_validates(self):
        with patch.object(policy, 'AI_FEATURES_ENABLED', True):
            gen = Mock()
            gen.model_dump.return_value = {'ops': []}
            generate, validate, set_job = Mock(return_value=gen), Mock(), Mock()
            worker = source_function(MAIN, '_run_amend_generation_job',
                generate_amend_ops_from_paragraphs=generate, validate_ops=validate,
                amend_gen_to_body_markdown=lambda gen: 'Operations', _set_amend_job=set_job)
            worker(job_id='job', plain_text='Notes', draft_symbol='L.1', draft_title='Draft',
                   agenda_label='Agenda', draft_text='Original')
            generate.assert_called_once()
            validate.assert_called_once_with('Operations')
            self.assertEqual(set_job.call_args.args[1]['status'], 'done')

    def test_enabled_translation_initializes_runtime_only_on_model_use(self):
        with patch.object(policy, 'AI_FEATURES_ENABLED', True), patch.dict(os.environ, {'TRANSLATE_DEVICE': 'cpu'}):
            translate = load_translation()
            self.assertIsNone(translate.torch)
            torch, transformers = MagicMock(), MagicMock()
            with patch.dict(sys.modules, {'torch': torch, 'transformers': transformers}):
                translate.warm_model('fr')
            transformers.AutoTokenizer.from_pretrained.assert_called_once_with('Helsinki-NLP/opus-mt-en-fr')
            transformers.AutoModelForSeq2SeqLM.from_pretrained.assert_called_once_with('Helsinki-NLP/opus-mt-en-fr')
            self.assertEqual(translate.current_translate_device(), 'cpu')

    def test_enabled_lookup_route_dispatches(self):
        with patch.object(policy, 'AI_FEATURES_ENABLED', True):
            req = Mock()
            request = Mock()
            request.json = AsyncMock(return_value={'text': 'Notes'})
            report = Mock()
            report.model_dump.return_value = {'items': []}
            run = AsyncMock(return_value=report)
            model = Mock()
            model.model_validate.return_value = req
            route = source_function('app/routers/ai_lookup.py', 'ai_lookup',
                current_user=lambda request: object(), LookupRequest=model, run_lookup=run)
            response = asyncio.run(route(request))
            self.assertEqual(response.status_code, 200)
            run.assert_awaited_once_with(req)


class TemplateTests(unittest.TestCase):
    def test_changed_templates_parse_and_summary_polling_is_conditional(self):
        env = Environment(loader=FileSystemLoader(ROOT/'app/templates'))
        names = ['rooms/show.html','events/draft_detail.html','events/amendment_detail.html',
                 'events/general_floor_item.html','events/proposal_floor_item.html']
        for name in names:
            source = (ROOT/'app/templates'/name).read_text()
            env.parse(source)
            if 'liveSummaryBody' in source:
                pos = source.index('const body = document.getElementById("liveSummaryBody");')
                start = source.rfind('{% if ai_features_enabled %}', 0, pos)
                end = source.index('{% endif %}', source.index('</script>', pos))+len('{% endif %}')
                script = env.from_string(source[start:end])
                self.assertNotIn('setInterval', script.render(ai_features_enabled=False))
                rendered = script.render(ai_features_enabled=True,
                    room=SimpleNamespace(id=1), item=SimpleNamespace(id=1),
                    event=SimpleNamespace(id=1), proposal=SimpleNamespace(id=2),
                    pfloor=SimpleNamespace(event_id=1, proposal_id=2),
                    draft=SimpleNamespace(id=3), mode='DRAFT')
                self.assertIn('setInterval', rendered)
            if 'translation_disabled' in source:
                start = source.index('{% if translation_disabled %}')
                end = source.index('{% endif %}',start)+len('{% endif %}')
                notice = env.from_string(source[start:end])
                self.assertIn('Translation is currently disabled', notice.render(translation_disabled=True))
                self.assertEqual(notice.render(translation_disabled=False), '')


if __name__ == '__main__':
    unittest.main()
