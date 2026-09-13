"""Small UI-only catalogues. No document translation or application services."""
import json
from pathlib import Path
from types import MappingProxyType
from urllib.parse import unquote, urlsplit

from jinja2 import pass_context

SUPPORTED_LOCALES = ("en", "ja")
DEFAULT_LOCALE = "en"
CATALOGUES = MappingProxyType({
    locale: MappingProxyType(json.loads(
        (Path(__file__).parent / "locales" / f"{locale}.json").read_text(encoding="utf-8")
    )) for locale in SUPPORTED_LOCALES
})
JS_KEYS = (
    'proposal_floor.summary_unavailable', 'proposal_floor.ror_invited_target',
    'floor.accepted',
    'floor.ahead',
    'floor.closed',
    'floor.current',
    'floor.declined',
    'floor.error',
    'floor.hint.accept',
    'floor.hint.decline',
    'floor.invitation_sent',
    'floor.invited',
    'floor.none_speaking',
    'floor.now',
    'floor.now_empty',
    'floor.open',
    'floor.queue.chair',
    'floor.queue.done',
    'floor.queue.general',
    'floor.queue.queued',
    'floor.queue.ror',
    'floor.queue.speaking',
    'floor.queue.withdrawn',
    'floor.remaining',
    'floor.reply_to',
    'floor.ror.accept',
    'floor.ror.all',
    'floor.ror.decline',
    'floor.ror.invited_all',
    'floor.ror.invited_target',
    'floor.sending',
    'floor.summary_unavailable',
    'floor.take_failed',
    'floor.take_network',
    'floor.updated',
    'floor.updating',

    "ui.notifications", "ui.unsaved_language", "common.ok", "notifications.floor_recognition",
    "dashboard.stage", "dashboard.in_progress", "dashboard.live", "dashboard.upcoming", "dashboard.ended",
    "dashboard.ended_seconds", "dashboard.ended_minutes", "dashboard.ended_hours", "dashboard.ended_days",
    "dashboard.countdown_days", "dashboard.countdown_hours",
    'event.locked',
    'event.open',
    'event.locks_on',
    'event.locked_since',
    'event.opens_on',
    'event.closed_since',
    'event.closes_on',
    'event.opened_at',
    'event.locked_before_vote',
    'event.locked_vote_start',
    'event.locks_before_vote',
    'event.locked_vote_end',
    'event.opens_voting',
)


def normalize_locale(value):
    return value if value in SUPPORTED_LOCALES else DEFAULT_LOCALE


def resolve_locale(request):
    for value in (request.headers.get("X-UI-Language"), request.cookies.get("ui_locale")):
        if value in SUPPORTED_LOCALES:
            return value
    return DEFAULT_LOCALE


def request_locale(request):
    if request is None:
        return DEFAULT_LOCALE
    value = getattr(request.state, "ui_locale", None)
    return normalize_locale(value) if value is not None else resolve_locale(request)


def translate(locale, key, **params):
    message = CATALOGUES[normalize_locale(locale)].get(key, CATALOGUES["en"].get(key, key))
    return message.format_map(params) if params else message


def safe_return_to(value):
    """Only root-relative local URLs; retain the original query string."""
    candidate = value or "/"
    decoded = candidate
    for _ in range(4):
        if (not decoded.startswith("/") or decoded.startswith("//")
                or "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded)):
            return "/"
        try:
            parsed = urlsplit(decoded)
        except ValueError:
            return "/"
        if parsed.scheme or parsed.netloc:
            return "/"
        next_value = unquote(decoded)
        if next_value == decoded:
            return candidate
        decoded = next_value
    return "/"


@pass_context
def template_locale(context):
    return request_locale(context.get("request"))


@pass_context
def template_translate(context, key, **params):
    return translate(template_locale(context), key, **params)


@pass_context
def javascript_catalogue(context):
    locale = template_locale(context)
    return {key: translate(locale, key) for key in JS_KEYS}


def install_jinja(env):
    env.globals.update(t=template_translate, ui_locale=template_locale,
                       ui_js_catalogue=javascript_catalogue)
