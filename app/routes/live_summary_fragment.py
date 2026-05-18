import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.db import get_session
from app.services.live_summary import Scope, refresh_summary_if_needed

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/live_summary")
async def api_live_summary(
    kind: str,
    question_id: int | None = None,
    room_id: int | None = None,
    event_id: int | None = None,
    proposal_id: int | None = None,
    draft_id: int | None = None,
    amendment_id: int | None = None,
):
    kind = kind.upper()
    if kind not in ("GENERAL", "PROOM", "PFLOOR"):
        raise HTTPException(400, "kind must be GENERAL|PROOM|PFLOOR")

    if kind == "GENERAL":
        if question_id is None:
            raise HTTPException(400, "question_id required for GENERAL")
        scope = Scope(kind="GENERAL", question_id=question_id)

    elif kind == "PROOM":
        if room_id is None:
            raise HTTPException(400, "room_id required for PROOM")
        scope = Scope(kind="PROOM", room_id=room_id)

    else:
        if event_id is None or proposal_id is None:
            raise HTTPException(400, "event_id and proposal_id required for PFLOOR")
        if (draft_id is None) == (amendment_id is None):
            raise HTTPException(400, "exactly one of draft_id or amendment_id is required for PFLOOR")
        scope = Scope(
            kind="PFLOOR",
            event_id=event_id,
            proposal_id=proposal_id,
            draft_id=draft_id,
            amendment_id=amendment_id,
        )

    try:
        with get_session() as db:
            row = await refresh_summary_if_needed(db, scope)
            return {
                "ok": True,
                "scope_key": row.scope_key,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "summary": row.summary or "",
            }

    except Exception as e:
        logger.warning("live summary API failed for %s: %s", kind, e)

        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "scope_key": None,
                "updated_at": None,
                "summary": "",
                "error": "Live summary temporarily unavailable",
            },
        )