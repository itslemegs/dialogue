"""Safe agenda formatting without app startup or a live database."""
import ast
from html.parser import HTMLParser
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock
from urllib.parse import urlsplit

from fastapi import Form, Request
from fastapi.responses import RedirectResponse
from jinja2 import Environment
from app.services.agenda_markdown import render_agenda_markdown
from tests import test_i18n as fixtures

ROOT = Path(__file__).resolve().parents[1]


class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__(); self.tags=[];self.feed(text)
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag,dict(attrs)))


class AgendaMarkdownTests(unittest.TestCase):
    def test_historical_plain_text_and_line_breaks(self):
        self.assertEqual(str(render_agenda_markdown('Original background\n第二行')),
                         '<p>Original background<br />\n第二行</p>\n')
        self.assertIn('A &amp; B &lt; C',render_agenda_markdown('A & B < C'))
        self.assertEqual(render_agenda_markdown(None),'')

    def test_bold_italic_lists_headings_and_quotes(self):
        html=render_agenda_markdown('## Background\n\n**bold** _italic_\n\n- one\n- two\n\n1. first\n2. second\n\n> context')
        for expected in ('<h2>Background</h2>','<strong>bold</strong>','<em>italic</em>',
                         '<ul>','<li>one</li>','<ol>','<blockquote>'):
            self.assertIn(expected,html)

    def test_links_use_safe_schemes_and_rel(self):
        for url in ('https://example.org','http://example.org','mailto:test@example.org','/events/1/menu'):
            html=render_agenda_markdown(f'[source]({url})')
            self.assertIn(f'href="{url}"',html)
            self.assertIn('rel="noopener noreferrer"',html)

    def test_code_fences_tables_and_rules(self):
        html=render_agenda_markdown('`code`\n\n```html\n<script>example</script>\n```\n\n---\n\n| A | B |\n| --- | --- |\n| 1 | 2 |')
        for text in ('<code>code</code>','<pre>','&lt;script&gt;','<hr','<table>'):
            self.assertIn(text,html)
        self.assertNotIn('<script>',html)

    def test_raw_html_handlers_and_unsafe_links_are_inert(self):
        cases=['<script>alert(1)</script>', '<img src=x onerror=alert(1)>',
               '<svg onload=alert(1)>', '<a href="javascript:alert(1)">x</a>',
               '[x](javascript:alert(1))', '[x](JaVaScRiPt:alert(1))',
               '[x](&#x6a;avascript:alert(1))', '[x](data:text/html,evil)',
               '[x](vbscript:msgbox(1))', '[x](file:///etc/passwd)',
               '![tracking](https://example.org/image.png)']
        for source in cases:
            with self.subTest(source=source):
                html=render_agenda_markdown(source)
                for tag,attrs in Tags(html).tags:
                    self.assertNotIn(tag,('script','img','svg','iframe','style'))
                    self.assertFalse(any(key.startswith('on') for key in attrs))
                    if 'href' in attrs:
                        self.assertIn(urlsplit(attrs['href']).scheme,('', 'https','http','mailto'))

    def test_real_agenda_pages_render_markdown_in_both_locales(self):
        fixture=fixtures.RequestTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        ctx=fixture.phase3_context()
        source='## Background\n\n**Original 日本語**\n\n<script>unsafe</script>'
        ctx['mine'][0].background=source
        for page in ('propose_agenda','review_agenda','view_agenda','general_floor'):
            for locale in ('en','ja'):
                html=fixture.render_phase2(f'events/{page}.html',locale,**ctx)
                self.assertIn('<h2>Background</h2>',html)
                self.assertIn('<strong>Original 日本語</strong>',html)
                self.assertNotIn('<script>unsafe</script>',html)
                self.assertEqual(ctx['mine'][0].background,source)
        floor=fixture.general_floor_context()
        floor['item']['background']=source
        html=fixture.render_phase2('events/general_floor_item.html','ja',**floor)
        self.assertIn('<strong>Original 日本語</strong>',html)

    def test_storage_and_textarea_remain_raw(self):
        source='## Background\n\n**authored**\n\n<script>inert</script>'
        tree=ast.parse((ROOT/'app/main.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='propose_agenda_post')
        node.decorator_list=[]
        db=MagicMock();db.__enter__.return_value=db
        def proposal(**kwargs):return SimpleNamespace(id=8,**kwargs)
        ns=dict(Request=Request,Form=Form,current_user=lambda r:SimpleNamespace(id=2),
                _normalize_url=lambda value:None,get_session=lambda:db,
                _require_event_access=lambda **kwargs:SimpleNamespace(id=1),AgendaProposal=proposal,
                ProposalStatus=SimpleNamespace(pending='pending'),slog=MagicMock(),RedirectResponse=RedirectResponse)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'isolated_agenda','exec'),ns)
        ns['propose_agenda_post'](object(),1,title='Title',background=source,source_url='',submit_anonymously=False)
        self.assertEqual(db.add.call_args.args[0].background,source)
        self.assertNotIn('<strong>',db.add.call_args.args[0].background)
        template=(ROOT/'app/templates/events/propose_agenda.html').read_text()
        textarea=re.search(r'<textarea\b[^>]*id="background".*?</textarea>',template,re.S)[0]
        self.assertNotIn('agenda_markdown',textarea)
        # A raw editor value remains escaped text, not rendered Markdown.
        env=Environment(autoescape=True);env.filters['agenda_markdown']=render_agenda_markdown
        value=env.from_string('<textarea>{{ background }}</textarea>').render(background=source)
        self.assertIn('**authored**',value);self.assertIn('&lt;script&gt;',value)
        self.assertNotIn('<strong>',value)
