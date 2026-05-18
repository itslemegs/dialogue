# app/services/help_ctx.py
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# ---- route matchers (based on your logs) ----
# Each matcher returns:
#   - screen: high-level area (for global guidance)
#   - ids: extracted IDs (question_id, draft_id, room_id, etc)
#   - subview: optional "fragment/state/head" kinds for AJAX endpoints

ROUTE_RULES = [
    # Top-level
    (re.compile(r"^/$"),                      {"screen": "HOME"}),
    (re.compile(r"^/login$"),                 {"screen": "LOGIN"}),
    (re.compile(r"^/dashboard$"),             {"screen": "DASHBOARD"}),

    # Notifications (ajax)
    (re.compile(r"^/notifications/pull$"),    {"screen": "NOTIFICATIONS", "subview": "PULL"}),

    # Events menu + agenda flows
    (re.compile(r"^/events/menu$"),           {"screen": "EVENTS_MENU"}),
    (re.compile(r"^/events/propose-agenda$"), {"screen": "AGENDA_PROPOSE"}),
    (re.compile(r"^/events/review-agenda$"),  {"screen": "AGENDA_REVIEW"}),
    (re.compile(r"^/events/view-agenda$"),    {"screen": "AGENDA_VIEW"}),

    # General floor index + scoped
    (re.compile(r"^/events/general-floor$"), {"screen": "GENERAL_FLOOR_INDEX"}),
    (re.compile(r"^/events/general-floor/(?P<question_id>\d+)$"),
        {"screen": "GENERAL_FLOOR"}),

    (re.compile(r"^/events/general-floor/(?P<question_id>\d+)/floor/state$"),
        {"screen": "GENERAL_FLOOR", "subview": "FLOOR_STATE"}),

    (re.compile(r"^/events/general-floor/(?P<question_id>\d+)/interventions/(?P<frag>head|fragment)$"),
        {"screen": "GENERAL_FLOOR", "subview_from_group": "frag"}),

    # Proposal discussion index + scoped + room
    (re.compile(r"^/events/proposal-discussion$"), {"screen": "PROPOSAL_DISCUSSION_INDEX"}),
    (re.compile(r"^/events/proposal-discussion/(?P<proposal_id>\d+)$"),
        {"screen": "PROPOSAL_DISCUSSION"}),

    (re.compile(r"^/events/proposal-discussion/(?P<proposal_id>\d+)/rooms/(?P<room_id>\d+)$"),
        {"screen": "PROPOSAL_ROOM"}),

    # Draft views
    (re.compile(r"^/events/view-draft$"), {"screen": "DRAFTS_INDEX"}),
    (re.compile(r"^/drafts/(?P<draft_id>\d+)$"), {"screen": "DRAFT_VIEW"}),

    # Proposal floor index + per-draft
    (re.compile(r"^/events/proposal-floor$"), {"screen": "PROPOSAL_FLOOR_INDEX"}),

    (re.compile(r"^/events/proposal-floor/draft/(?P<draft_id>\d+)$"),
        {"screen": "PROPOSAL_FLOOR_DRAFT"}),

    (re.compile(r"^/events/proposal-floor/draft/(?P<draft_id>\d+)/floor/state$"),
        {"screen": "PROPOSAL_FLOOR_DRAFT", "subview": "FLOOR_STATE"}),

    (re.compile(r"^/events/proposal-floor/draft/(?P<draft_id>\d+)/interventions/(?P<frag>head|fragment)$"),
        {"screen": "PROPOSAL_FLOOR_DRAFT", "subview_from_group": "frag"}),

    # (future-proof) if you later add amendment scope
    (re.compile(r"^/events/proposal-floor/amendment/(?P<amendment_id>\d+)$"),
        {"screen": "PROPOSAL_FLOOR_AMENDMENT"}),

    # Decision screen
    (re.compile(r"^/events/decision$"), {"screen": "DECISION"}),

    # Live summary API (querystring carries ids)
    (re.compile(r"^/api/live_summary$"), {"screen": "LIVE_SUMMARY_API"}),
]


def _int(x: Any) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def build_help_ctx(request, flags: Optional[Dict[str, bool]] = None, stage: Optional[str] = None) -> Dict[str, Any]:
    """
    Build a single HELP_CTX object for *any* route in your app.
    - Extracts IDs from path (and from querystring if present)
    - Assigns a 'screen' string that your copilot can use for system-wide guidance
    """
    path = request.url.path
    qp = getattr(request, "query_params", {}) or {}

    ctx: Dict[str, Any] = {
        "route": path,
        "screen": "UNKNOWN",
        "subview": None,
        "stage": stage or "UNKNOWN",
        "flags": flags or {},

        # common ids (fill from url or query)
        "event_id": _int(qp.get("event_id")),
        "proposal_id": _int(qp.get("proposal_id")),
        "question_id": _int(qp.get("question_id")),
        "room_id": _int(qp.get("room_id")),
        "draft_id": _int(qp.get("draft_id")),
        "amendment_id": _int(qp.get("amendment_id")),

        # extra bits you might want later
        "live_kind": qp.get("kind"),  # e.g., GENERAL / PFLOOR (from /api/live_summary)
    }

    for rx, rule in ROUTE_RULES:
        m = rx.match(path)
        if not m:
            continue

        ctx["screen"] = rule.get("screen", "UNKNOWN")

        # ids from regex named groups
        gd = m.groupdict()
        for key in ("event_id", "proposal_id", "question_id", "room_id", "draft_id", "amendment_id"):
            if key in gd and gd[key] is not None:
                ctx[key] = _int(gd[key])

        # optional subview
        if "subview" in rule:
            ctx["subview"] = rule["subview"]
        elif "subview_from_group" in rule:
            gname = rule["subview_from_group"]
            ctx["subview"] = gd.get(gname)

        break

    return ctx


def merge_help_ctx(base: Dict[str, Any], extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Optional patch helper. Use only if you *really* need to override IDs on a page.
    """
    if not extra:
        return base
    out = dict(base)
    for k, v in extra.items():
        if v is not None:
            out[k] = v
    return out
