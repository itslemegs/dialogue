"""Safe Markdown for agenda backgrounds only; never used for storage."""
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markupsafe import Markup


def _safe_link(url):
    # markdown-it normalizes/unescapes link destinations before validation.
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        return False
    try:
        return urlsplit(url).scheme.lower() in ('', 'http', 'https', 'mailto')
    except ValueError:
        return False


def _link_open(tokens, idx, options, env):
    tokens[idx].attrSet('rel', 'noopener noreferrer')
    return _MARKDOWN.renderer.renderToken(tokens, idx, options, env)


# No HTML passthrough, image tags, plugins, or untrusted rendering callbacks.
# Plain-text line breaks retain the previous whitespace-pre-wrap presentation.
_MARKDOWN = MarkdownIt('commonmark', {'html': False, 'breaks': True}).enable('table').disable('image')
_MARKDOWN.validateLink = _safe_link
_MARKDOWN.renderer.rules['link_open'] = _link_open


def render_agenda_markdown(source):
    """Only the output of the restricted renderer is trusted as HTML."""
    return Markup(_MARKDOWN.render(str(source or '')))
