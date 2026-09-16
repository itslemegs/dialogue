"""Presentation-only grouping of the existing, bounded session-log result set."""
import json

from app.i18n import translate
from app.services.session_log import page_log_context


def activity_for(record, details):
    phase = record.get('phase') or details.get('phase')
    if not phase:
        phase = page_log_context(record.get('page')).get('phase')
    if phase in ('agenda_setting', 'agenda_review'):
        return 'agenda'
    if phase == 'general_floor':
        return 'general_floor'
    if phase == 'proposal_floor':
        return 'proposal_floor'
    if phase == 'decision':
        return 'decision'
    action = record.get('action') or ''
    target = (record.get('target_type') or '').lower()
    kind = details.get('page_kind')
    if phase in ('proposal_discussion', 'translation') or not phase:
        if target == 'amendment' or kind == 'amendment_detail' or action.startswith('AMENDMENT_'):
            return 'amendment'
        if target in ('draft', 'proposal_draft') or kind == 'draft_detail' or action.startswith('DRAFT_'):
            return 'drafting'
        if phase == 'proposal_discussion' or action.startswith('PROPOSAL_ROOM_'):
            return 'proposal_discussion'
    if not phase:
        for prefixes, activity in [
            (('GENERAL_',), 'general_floor'),
            (('PFLOOR_', 'EARLY_VOTE_', 'FORMAL_VOTE_'), 'proposal_floor'),
            (('AGENDA_',), 'agenda'),
        ]:
            if action.startswith(prefixes):
                return activity
    return 'other'


def build_log_view(records, user_map, locale):
    """Input retains SQL's timestamp DESC / id DESC ordering. No DB access."""
    groups = {}
    rows = []
    for record in records:
        details = record.get('details')
        details = details if isinstance(details, dict) else {}
        activity = activity_for(record, details)
        raw_action = record.get('action') or ''
        label_key = {
            'SUBMIT_DRAFT_AS_L_DOC': 'logs.viewer.submit_attempt',
            'HTTP_REQUEST': 'logs.viewer.http_request',
        }.get(raw_action)
        action_label = translate(locale, label_key) if label_key else raw_action.replace('_', ' ').capitalize()
        user_id = record.get('user_id')
        user = user_map.get(user_id)
        user_label = f'@{user.handle} ({user_id})' if user else str(user_id or '—')
        context = []
        for key in ('proposal_id', 'room_id', 'draft_id', 'amendment_id', 'question_id'):
            value = details.get(key)
            if key == 'proposal_id' and value is None:
                value = details.get('agenda_proposal_id')
            if value is not None and value != '':
                context.append((key, str(value)))
        row = dict(record, activity=activity, action_label=action_label,
                   user_label=user_label, context=context,
                   raw_json=json.dumps(record, ensure_ascii=False, indent=2, default=str))
        row['search'] = ' '.join(str(record.get(k) or '') for k in (
            'action', 'page', 'route', 'target_type', 'target_id', 'user_id', 'session_key', 'phase'
        )) + ' ' + action_label + ' ' + user_label + ' ' + ' '.join(v for _, v in context)
        groups.setdefault(activity, []).append(row)
        rows.append(row)
    return dict(groups=groups, rows=rows,
                actions=sorted({r['action'] or '' for r in rows}),
                targets=sorted({r['target_type'] or '' for r in rows}),
                sessions=len({r['session_key'] for r in rows if r['session_key']}),
                semantic=sum(r['source'] == 'semantic' for r in rows),
                client=sum(r['source'] == 'client' for r in rows))
