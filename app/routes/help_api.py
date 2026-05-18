from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Any, Dict, List

from app.services.help_copilot import ollama_chat_json
from app.services.auth import current_user  # whatever you use

router = APIRouter()

class HelpAsk(BaseModel):
    question: str
    # you can also pass route/stage from the frontend if needed
    route: str | None = None

def compute_allowed_actions(flags: Dict[str, bool], stage: str) -> List[Dict[str, Any]]:
    # IMPORTANT: this is your guardrail. Only actions from here may be suggested.
    actions = [{"type": "NAV", "to": "/events", "label": "Back to Event"}]

    if stage == "FLOOR":
        actions += [{"type": "NAV", "to": "/floor", "label": "Open Speakers List"}]
        if flags.get("IS_MEMBER"):
            actions += [{"type": "OPEN_MODAL", "id": "request_floor", "label": "Request the Floor"}]
        if flags.get("IS_CHAIR") or flags.get("IS_PRESIDENT"):
            actions += [{"type": "OPEN_MODAL", "id": "manage_queue", "label": "Manage Queue"}]

    # add stages for DRAFTING / AMENDMENT / VOTING etc.
    return actions

@router.post("/api/help/ask")
async def help_ask(request: Request, body: HelpAsk):
    user = current_user(request)
    flags = getattr(request.state, "flags", None)  # however you set this
    if not flags:
        flags = {}

    stage = getattr(request.state, "stage", "UNKNOWN")
    route = body.route or str(request.url.path)

    app_state = {
        "route": route,
        "stage": stage,
        "flags": {
            "IS_ADMIN": bool(getattr(flags, "IS_ADMIN", False)),
            "IS_CHAIR": bool(getattr(flags, "IS_CHAIR", False)),
            "IS_PRESIDENT": bool(getattr(flags, "IS_PRESIDENT", False)),
            "IS_MEMBER": bool(getattr(flags, "IS_MEMBER", False)),
        },
        # add anything that reduces ambiguity:
        # "speaker_queue_count": ...,
        # "active_draft": ...,
        # "vote_open": ...,
    }

    allowed_actions = compute_allowed_actions(app_state["flags"], stage)
    allowed_actions_compact = [
        {"label": a.get("label", ""), "action": {"type": a["type"], **{k:v for k,v in a.items() if k in ("to","id")}}}
        for a in allowed_actions
    ]

    out = await ollama_chat_json(app_state, allowed_actions_compact, body.question)
    return out
