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
    # Guided-tour controls and page-specific steps.
    "tutorial.dashboard.your_dashboard_title",
    "tutorial.dashboard.your_dashboard_text",
    "tutorial.dashboard.events_title",
    "tutorial.dashboard.events_text",
    "tutorial.dashboard.open_an_event_title",
    "tutorial.dashboard.open_an_event_text",
    "tutorial.dashboard.event_status_title",
    "tutorial.dashboard.event_status_text",
    "tutorial.dashboard.upcoming_event_title",
    "tutorial.dashboard.upcoming_event_text",
    "tutorial.dashboard.live_event_title",
    "tutorial.dashboard.live_event_text",
    "tutorial.dashboard.ended_event_title",
    "tutorial.dashboard.ended_event_text",
    "tutorial.dashboard.event_stages_title",
    "tutorial.dashboard.event_stages_text",
    "tutorial.dashboard.department_of_records_title",
    "tutorial.dashboard.department_of_records_text",
    "tutorial.event_menu.event_menu_title",
    "tutorial.event_menu.event_menu_text",
    "tutorial.event_menu.current_event_title",
    "tutorial.event_menu.current_event_text",
    "tutorial.event_menu.event_workflow_title",
    "tutorial.event_menu.event_workflow_text",
    "tutorial.event_menu.propose_agenda_title",
    "tutorial.event_menu.propose_agenda_text",
    "tutorial.event_menu.review_agenda_title",
    "tutorial.event_menu.review_agenda_text",
    "tutorial.event_menu.view_agenda_title",
    "tutorial.event_menu.view_agenda_text",
    "tutorial.event_menu.general_floor_title",
    "tutorial.event_menu.general_floor_text",
    "tutorial.event_menu.proposal_discussion_title",
    "tutorial.event_menu.proposal_discussion_text",
    "tutorial.event_menu.view_tabled_drafts_title",
    "tutorial.event_menu.view_tabled_drafts_text",
    "tutorial.event_menu.proposal_floor_title",
    "tutorial.event_menu.proposal_floor_text",
    "tutorial.event_menu.decision_title",
    "tutorial.event_menu.decision_text",
    "tutorial.event_menu.open_and_locked_stages_title",
    "tutorial.event_menu.open_and_locked_stages_text",
    "tutorial.propose_agenda.propose_an_agenda_item_title",
    "tutorial.propose_agenda.propose_an_agenda_item_text",
    "tutorial.propose_agenda.agenda_title_title",
    "tutorial.propose_agenda.agenda_title_text",
    "tutorial.propose_agenda.background_title",
    "tutorial.propose_agenda.background_text",
    "tutorial.propose_agenda.supporting_source_title",
    "tutorial.propose_agenda.supporting_source_text",
    "tutorial.propose_agenda.anonymous_submission_title",
    "tutorial.propose_agenda.anonymous_submission_text",
    "tutorial.propose_agenda.submit_for_review_title",
    "tutorial.propose_agenda.submit_for_review_text",
    "tutorial.propose_agenda.your_proposals_title",
    "tutorial.propose_agenda.your_proposals_text",
    "tutorial.review_agenda.pending_agenda_proposals_title",
    "tutorial.review_agenda.pending_agenda_proposals_text",
    "tutorial.review_agenda.review_the_proposal_title",
    "tutorial.review_agenda.review_the_proposal_text",
    "tutorial.review_agenda.review_note_title",
    "tutorial.review_agenda.review_note_text",
    "tutorial.review_agenda.accept_title",
    "tutorial.review_agenda.accept_text",
    "tutorial.review_agenda.reject_title",
    "tutorial.review_agenda.reject_text",
    "tutorial.review_agenda.recent_decisions_title",
    "tutorial.review_agenda.recent_decisions_text",
    "tutorial.review_agenda.reopen_a_decision_title",
    "tutorial.review_agenda.reopen_a_decision_text",
    "tutorial.view_agenda.accepted_agenda_title",
    "tutorial.view_agenda.accepted_agenda_text",
    "tutorial.view_agenda.agenda_item_title",
    "tutorial.view_agenda.agenda_item_text",
    "tutorial.view_agenda.supporting_source_title",
    "tutorial.view_agenda.supporting_source_text",
    "tutorial.view_agenda.accepted_status_title",
    "tutorial.view_agenda.accepted_status_text",
    "tutorial.general_floor_index.general_floor_title",
    "tutorial.general_floor_index.general_floor_text",
    "tutorial.general_floor_index.current_event_title",
    "tutorial.general_floor_index.current_event_text",
    "tutorial.general_floor_index.accepted_agenda_items_title",
    "tutorial.general_floor_index.accepted_agenda_items_text",
    "tutorial.general_floor_index.agenda_item_title",
    "tutorial.general_floor_index.agenda_item_text",
    "tutorial.general_floor_index.agenda_information_title",
    "tutorial.general_floor_index.agenda_information_text",
    "tutorial.general_floor_index.enter_the_general_floor_title",
    "tutorial.general_floor_index.enter_the_general_floor_text",
    "tutorial.general_floor.current_agenda_title",
    "tutorial.general_floor.current_agenda_text",
    "tutorial.general_floor.discussion_title",
    "tutorial.general_floor.discussion_text",
    "tutorial.general_floor.chair_controls_title",
    "tutorial.general_floor.chair_controls_text",
    "tutorial.general_floor.your_contribution_title",
    "tutorial.general_floor.your_contribution_text",
    "tutorial.general_floor.live_summary_title",
    "tutorial.general_floor.live_summary_text",
    "tutorial.general_floor.speaker_queue_title",
    "tutorial.general_floor.speaker_queue_chair_text",
    "tutorial.general_floor.speaker_queue_member_text",
    "tutorial.proposal_discussion.proposal_discussions_title",
    "tutorial.proposal_discussion.proposal_discussions_text",
    "tutorial.proposal_discussion.choose_an_agenda_item_title",
    "tutorial.proposal_discussion.choose_an_agenda_item_text",
    "tutorial.proposal_discussion.view_existing_rooms_title",
    "tutorial.proposal_discussion.view_existing_rooms_text",
    "tutorial.proposal_discussion.create_a_discussion_room_title",
    "tutorial.proposal_discussion.create_a_discussion_room_text",
    "tutorial.proposal_discussion.start_the_room_title",
    "tutorial.proposal_discussion.start_the_room_text",
    "tutorial.rooms_index.create_a_discussion_room_title",
    "tutorial.rooms_index.create_a_discussion_room_text",
    "tutorial.rooms_index.existing_rooms_title",
    "tutorial.rooms_index.existing_rooms_text",
    "tutorial.rooms_index.room_title",
    "tutorial.rooms_index.room_text",
    "tutorial.rooms_index.room_sponsor_title",
    "tutorial.rooms_index.room_sponsor_text",
    "tutorial.rooms_index.open_the_room_title",
    "tutorial.rooms_index.open_the_room_text",
    "tutorial.room.room_discussion_title",
    "tutorial.room.room_discussion_text",
    "tutorial.room.shared_draft_title",
    "tutorial.room.shared_draft_text",
    "tutorial.room.drafting_guide_title",
    "tutorial.room.drafting_guide_text",
    "tutorial.room.co_sign_the_draft_title",
    "tutorial.room.co_sign_the_draft_text",
    "tutorial.room.sponsor_and_co_signers_title",
    "tutorial.room.sponsor_and_co_signers_text",
    "tutorial.room.editing_workspace_title",
    "tutorial.room.editing_workspace_text",
    "tutorial.room.ai_drafting_title",
    "tutorial.room.ai_drafting_text",
    "tutorial.room.manual_drafting_title",
    "tutorial.room.manual_drafting_text",
    "tutorial.room.save_your_work_title",
    "tutorial.room.save_your_work_text",
    "tutorial.room.submit_the_draft_title",
    "tutorial.room.submit_the_draft_text",
    "tutorial.view_draft_index.tabled_draft_resolutions_title",
    "tutorial.view_draft_index.tabled_draft_resolutions_text",
    "tutorial.view_draft_index.active_drafts_title",
    "tutorial.view_draft_index.active_drafts_text",
    "tutorial.view_draft_index.open_a_draft_title",
    "tutorial.view_draft_index.open_a_draft_text",
    "tutorial.view_draft_index.reintroduced_draft_title",
    "tutorial.view_draft_index.reintroduced_draft_text",
    "tutorial.view_draft_index.withdrawn_drafts_title",
    "tutorial.view_draft_index.withdrawn_drafts_text",
    "tutorial.view_draft_index.withdrawn_draft_title",
    "tutorial.view_draft_index.withdrawn_draft_text",
    "tutorial.view_draft_index.reintroduce_a_draft_title",
    "tutorial.view_draft_index.reintroduce_a_draft_text",
    "tutorial.draft_detail.official_draft_title",
    "tutorial.draft_detail.official_draft_text",
    "tutorial.draft_detail.co_signers_title",
    "tutorial.draft_detail.co_signers_text",
    "tutorial.draft_detail.late_co_signing_title",
    "tutorial.draft_detail.late_co_signing_text",
    "tutorial.draft_detail.existing_amendments_title",
    "tutorial.draft_detail.existing_amendments_text",
    "tutorial.draft_detail.propose_an_amendment_title",
    "tutorial.draft_detail.propose_an_amendment_text",
    "tutorial.draft_detail.how_amendments_work_title",
    "tutorial.draft_detail.how_amendments_work_text",
    "tutorial.draft_detail.describe_the_change_title",
    "tutorial.draft_detail.describe_the_change_text",
    "tutorial.draft_detail.generate_operations_title",
    "tutorial.draft_detail.generate_operations_text",
    "tutorial.draft_detail.add_an_operation_title",
    "tutorial.draft_detail.add_an_operation_text",
    "tutorial.draft_detail.amendment_operations_title",
    "tutorial.draft_detail.amendment_operations_text",
    "tutorial.draft_detail.submit_the_amendment_title",
    "tutorial.draft_detail.submit_the_amendment_text",
    "tutorial.amendment_detail.amendment_title",
    "tutorial.amendment_detail.amendment_text",
    "tutorial.amendment_detail.translation_title",
    "tutorial.amendment_detail.translation_text",
    "tutorial.amendment_detail.original_draft_title",
    "tutorial.amendment_detail.original_draft_text",
    "tutorial.amendment_detail.proposed_changes_title",
    "tutorial.amendment_detail.proposed_changes_text",
    "tutorial.proposal_floor_index.proposal_floor_title",
    "tutorial.proposal_floor_index.proposal_floor_text",
    "tutorial.proposal_floor_index.open_proposal_floors_title",
    "tutorial.proposal_floor_index.open_proposal_floors_text",
    "tutorial.proposal_floor_index.draft_resolution_title",
    "tutorial.proposal_floor_index.draft_resolution_text",
    "tutorial.proposal_floor_index.sponsor_title",
    "tutorial.proposal_floor_index.sponsor_text",
    "tutorial.proposal_floor_index.open_the_draft_floor_title",
    "tutorial.proposal_floor_index.open_the_draft_floor_text",
    "tutorial.proposal_floor_index.amendments_title",
    "tutorial.proposal_floor_index.amendments_text",
    "tutorial.proposal_floor_index.closed_proposal_floors_title",
    "tutorial.proposal_floor_index.closed_proposal_floors_text",
    "tutorial.proposal_floor_index.closed_draft_title",
    "tutorial.proposal_floor_index.closed_draft_text",
    "tutorial.proposal_floor_index.closed_title",
    "tutorial.proposal_floor_index.closed_text",
    "tutorial.proposal_floor.current_document_title",
    "tutorial.proposal_floor.current_document_text",
    "tutorial.proposal_floor.voting_title",
    "tutorial.proposal_floor.voting_text",
    "tutorial.proposal_floor.discussion_title",
    "tutorial.proposal_floor.discussion_text",
    "tutorial.proposal_floor.speaker_queue_title",
    "tutorial.proposal_floor.speaker_queue_text",
    "tutorial.proposal_floor.speaking_title",
    "tutorial.proposal_floor.speaking_text",
    "tutorial.decision.decision_title",
    "tutorial.decision.decision_text",
    "tutorial.decision.final_results_title",
    "tutorial.decision.final_results_text",
    "tutorial.decision.draft_resolutions_title",
    "tutorial.decision.draft_resolutions_text",
    "tutorial.decision.draft_result_title",
    "tutorial.decision.draft_result_text",
    "tutorial.decision.outcome_title",
    "tutorial.decision.outcome_text",
    "tutorial.decision.early_consensus_test_title",
    "tutorial.decision.early_consensus_test_text",
    "tutorial.decision.formal_vote_title",
    "tutorial.decision.formal_vote_text",
    "tutorial.decision.amendments_title",
    "tutorial.decision.amendments_text",
    "tutorial.decision.amendment_result_title",
    "tutorial.decision.amendment_result_text",
    "tutorial.decision.amendment_outcome_title",
    "tutorial.decision.amendment_outcome_text",
    "tutorial.decision.amendment_vote_title",
    "tutorial.decision.amendment_vote_text",
    "tutorial.common.next",
    "tutorial.common.back",
    "tutorial.common.skip",
    "tutorial.common.done",
    "tutorial.common.close",
    "tutorial.common.step",

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
