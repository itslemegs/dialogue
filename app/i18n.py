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
    "notifications.announcement", "roles.chairman",
    'notifications.recognized',
    'notifications.ror_target',
    'notifications.intro',
    'notifications.ror_accepted',
    'notifications.ror_declined',

    "admin.confirm_delete",
    'document.confirm_withdraw',
    'draft.preambular',
    'draft.operative',
    'amendment.action.add',
    'amendment.action.remove',
    'amendment.action.replace',
    'amendment.target_example',
    'amendment.content_placeholder',
    'amendment.remove_operation',
    'amendment.operation',
    'amendment.action_prompt',
    'amendment.section_prompt',
    'amendment.target',
    'amendment.target_help',
    'amendment.content',
    'amendment.content_help',
    'amendment.ai.empty',
    'amendment.ai.starting',
    'amendment.ai.no_operations',
    'amendment.ai.done',
    'amendment.error.empty_operations',
    'amendment.ai.failed',
    'amendment.ai.timeout',
    'amendment.ai.invalid_result',
    'amendment.error.instruction',
    'amendment.error.job',
    'amendment.error.unsubmitted',
    'amendment.error.withdrawn',
    'amendment.error.adopted',
    'amendment.error.clause',
    'amendment.error.target',
    'amendment.error.content',
    'document.translation_failed',
    'document.try_later',

    'proposal_discussion.confirm_delete',
    'proposal_discussion.confirm_delete_all',
    'draft.ai.elapsed',
    'draft.ai.empty',
    'draft.ai.generating',
    'draft.ai.starting',
    'draft.ai.wait',
    'draft.ai.done',
    'draft.ai.generate',
    'draft.ai.failed',
    'draft.ai.failed_detail',
    'draft.ai.still_running',
    'draft.ai.not_started',
    'draft.ai.sponsor_only',
    'draft.ai.status_sponsor_only',
    'draft.error.submitted',
    'draft.ai.job_missing',
    'draft.ai.busy',

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


# Known application UI messages only; machine/provider details pass through.
UI_ERROR_KEYS = {
    'Unauthorized': 'error.unauthorized',
    'Forbidden': 'error.forbidden',
    'Not Found': 'error.not_found',
    'Method Not Allowed': 'error.method',

    'Event not found': 'error.event_missing',
    'Event access denied': 'error.event_access',
    'Members only': 'error.members',
    'Your account is banned': 'account.banned',
    'Banned users cannot access this resource': 'error.banned_resource',
    'Agenda item not found for this event': 'error.agenda_event',
    'Agenda item not found or not accepted': 'error.agenda_missing',
    'Agenda item not accepted': 'error.agenda_unaccepted',
    'Room not found for this event': 'error.room_event',
    'Draft not found for this event': 'error.draft_event',
    'Amendment not found': 'error.amendment_missing',
    'Amendment not found for this event': 'error.amendment_event',
    'Admins only': 'error.admins',
    'Admins or presidents or chairmen only': 'error.managers',
    'role required': 'error.role_required',
    'You cannot modify your own roles': 'error.own_roles',
    'Cannot modify roles of an admin': 'error.admin_roles',
    "Presidents may only grant 'chairman' or 'invited speaker'": 'error.president_grant',
    "Presidents may only revoke 'chairman' or 'invited speaker'": 'error.president_revoke',
    "Chairmen may only grant 'invited speaker'": 'error.chair_grant',
    "Chairmen may only revoke 'invited speaker'": 'error.chair_revoke',
    "Cannot revoke baseline 'member' role": 'error.baseline',
    'Insufficient role': 'error.role',
    'You are not allowed to ban this user': 'error.ban',
    'You are not allowed to unban this user': 'error.unban',
    'President/Chairman access required': 'error.chair_access',
    'Invalid action': 'error.action',
    'Invalid start datetime': 'admin.error.start',
    'Invalid access mode': 'admin.error.access',
    'Passcode is required for private events': 'admin.error.passcode',
    'Opening must be at least 1 minute.': 'admin.error.opening',
    'General Debate must be at least 1 minute.': 'admin.error.debate',
    'Voting must be at least 1 minute.': 'admin.error.voting',
    'Draft not found': 'document.error.not_found',
    'This draft is not visible yet.': 'document.error.not_visible',
    'Could not allocate local number; please retry.': 'error.number',
    'Voting is closed': 'error.vote_closed',
    'Already voted': 'error.already_voted',
    'Invalid choice': 'error.choice',
    'Draft is not visible yet': 'error.draft_visibility',
    'You do not currently have the floor.': 'floor.error.not_speaking',
    'Only president or chairman can invite RoR': 'floor.error.invite',
    'User handle not found': 'floor.error.handle',
    'Target intervention not found in this proposal floor': 'error.target',
    'Speaker list is closed. Use Right of Reply.': 'error.list_closed',
    'Experiment logs are restricted to admins, presidents, and chairmen': 'error.logs',
}


def localize_ui_error(locale, message):
    if not isinstance(message, str):
        return message
    key = UI_ERROR_KEYS.get(message)
    return translate(locale, key) if key else message


@pass_context
def template_error(context, message):
    return localize_ui_error(template_locale(context), message)


@pass_context
def role_label(context, role):
    keys = {"admin": "roles.admin", "president": "roles.president",
            "chairman": "roles.chairman", "invited speaker": "roles.invited_speaker",
            "member": "roles.member", "banned": "roles.banned"}
    return template_translate(context, keys[role]) if role in keys else role


def install_jinja(env):
    env.globals.update(t=template_translate, ui_locale=template_locale,
                       ui_js_catalogue=javascript_catalogue, ui_error=template_error)
    env.filters["role_label"] = role_label
