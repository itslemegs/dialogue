# app/routes/session_log.py

from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlmodel import select

from app.db import get_session
from app.models import ExperimentSessionLog, User
from app.services.session_log import add_session_log
from app.security import unsign_cookie


router = APIRouter()


def _require_user(request: Request):
    """
    Replace this if your current_user() lives somewhere else.
    This assumes your existing app has current_user(request) in app.main.
    """
    try:
        from app.main import current_user

        user = current_user(request)
    except Exception:
        user = None

    if not user:
        raise HTTPException(status_code=401)

    return user

def _user_id_from_session_cookie(request: Request) -> int | None:
    """
    Get logged-in user id from the signed session cookie.
    Never crash logging if the cookie is missing/bad.
    """
    try:
        return unsign_cookie(request.cookies.get("session"))
    except Exception:
        return None


def _log_to_dict(log: ExperimentSessionLog) -> dict[str, Any]:
    try:
        details = json.loads(log.details_json) if log.details_json else None
    except Exception:
        details = log.details_json

    return {
        "id": log.id,
        "created_at": log.created_at.isoformat() if isinstance(log.created_at, datetime) else str(log.created_at),
        "event_id": log.event_id,
        "user_id": log.user_id,
        "session_key": log.session_key,
        "source": log.source,
        "action": log.action,
        "page": log.page,
        "route": log.route,
        "method": log.method,
        "status_code": log.status_code,
        "duration_ms": log.duration_ms,
        "phase": log.phase,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "ip_hash": log.ip_hash,
        "user_agent": log.user_agent,
        "request_id": log.request_id,
        "details": details,
    }


@router.post("/api/session-log/client")
async def client_session_log(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    event_id = payload.get("event_id")
    try:
        event_id = int(event_id) if event_id not in (None, "") else None
    except Exception:
        event_id = None

    payload_user_id = payload.get("user_id")
    try:
        payload_user_id = int(payload_user_id) if payload_user_id not in (None, "") else None
    except Exception:
        payload_user_id = None

    cookie_user_id = _user_id_from_session_cookie(request)
    effective_user_id = cookie_user_id or payload_user_id

    details = dict(payload.get("details") or {})

    if payload.get("user_handle"):
        details["user_handle"] = payload.get("user_handle")

    with get_session() as db:
        add_session_log(
            db,
            request=request,
            user_id=effective_user_id,
            event_id=event_id,
            source="client",
            action=payload.get("action") or "CLIENT_EVENT",
            page=payload.get("page"),
            phase=payload.get("phase"),
            target_type=payload.get("target_type"),
            target_id=payload.get("target_id"),
            details=details,
        )
        db.commit()

    return {"ok": True}


@router.get("/events/{event_id}/session-log/export")
def export_session_log(event_id: int, request: Request, format: str = "csv"):
    _require_user(request)

    with get_session() as db:
        logs = db.exec(
            select(ExperimentSessionLog)
            .where(ExperimentSessionLog.event_id == event_id)
            .order_by(ExperimentSessionLog.created_at, ExperimentSessionLog.id)
        ).all()

        user_ids = sorted({log.user_id for log in logs if log.user_id})
        users = db.exec(select(User).where(User.id.in_(user_ids))).all() if user_ids else []
        user_map = {u.id: u for u in users}

    rows = []

    for log in logs:
        row = _log_to_dict(log)

        user_label = ""
        user_handle = ""

        if log.user_id:
            u = user_map.get(log.user_id)
            if u:
                user_handle = u.handle or ""
                user_label = f"@{u.handle} ({log.user_id})"
            else:
                user_label = str(log.user_id)

        row["user_handle"] = user_handle
        row["user_label"] = user_label

        rows.append(row)

    if format.lower() == "json":
        return JSONResponse(rows)

    output = io.StringIO()

    fieldnames = [
        "id",
        "created_at",
        "event_id",
        "user_id",
        "user_handle",
        "user_label",
        "session_key",
        "source",
        "action",
        "page",
        "route",
        "method",
        "status_code",
        "duration_ms",
        "phase",
        "target_type",
        "target_id",
        "ip_hash",
        "user_agent",
        "request_id",
        "details",
    ]

    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for row in rows:
        row = dict(row)
        row["details"] = json.dumps(row.get("details"), ensure_ascii=False, default=str)
        writer.writerow(row)

    filename = f"event_{event_id}_session_log.csv"

    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


@router.get("/events/{event_id}/session-log", response_class=HTMLResponse)
def view_session_log(event_id: int, request: Request):
    _require_user(request)

    with get_session() as db:
        logs = db.exec(
            select(ExperimentSessionLog)
            .where(ExperimentSessionLog.event_id == event_id)
            .order_by(ExperimentSessionLog.created_at.desc(), ExperimentSessionLog.id.desc())
            .limit(300)
        ).all()

        user_ids = sorted({log.user_id for log in logs if log.user_id})
        users = db.exec(select(User).where(User.id.in_(user_ids))).all() if user_ids else []
        user_map = {u.id: u for u in users}

    rows_html = []

    for log in logs:
        details = log.details_json or ""

        user_label = ""
        if log.user_id:
            u = user_map.get(log.user_id)
            if u:
                user_label = f"@{u.handle} ({log.user_id})"
            else:
                user_label = str(log.user_id)

        rows_html.append(
            f"""
            <tr>
                <td>{html.escape(str(log.created_at))}</td>
                <td>{html.escape(user_label)}</td>
                <td>{html.escape(str(log.session_key or ""))[:8]}</td>
                <td>{html.escape(log.source or "")}</td>
                <td><strong>{html.escape(log.action or "")}</strong></td>
                <td>{html.escape(log.phase or "")}</td>
                <td>{html.escape(log.page or "")}</td>
                <td>{html.escape(log.method or "")}</td>
                <td>{html.escape(str(log.status_code or ""))}</td>
                <td>{html.escape(str(log.duration_ms or ""))}</td>
                <td>{html.escape(log.target_type or "")}</td>
                <td>{html.escape(log.target_id or "")}</td>
                <td><pre>{html.escape(details[:500])}</pre></td>
            </tr>
            """
        )

    return f"""
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Session Log - Event {event_id}</title>
        <style>
            body {{
                font-family: system-ui, sans-serif;
                padding: 24px;
                background: #f7f7f7;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                background: white;
                font-size: 13px;
            }}
            th, td {{
                border: 1px solid #ddd;
                padding: 8px;
                vertical-align: top;
            }}
            th {{
                background: #eee;
                position: sticky;
                top: 0;
            }}
            pre {{
                white-space: pre-wrap;
                max-width: 420px;
                margin: 0;
            }}
            .links {{
                margin-bottom: 16px;
            }}
            a {{
                margin-right: 12px;
            }}
        </style>
    </head>
    <body>
        <h1>Session Log - Event {event_id}</h1>

        <div class="links">
            <a href="/events/{event_id}/session-log/export?format=csv">Download CSV</a>
            <a href="/events/{event_id}/session-log/export?format=json">Download JSON</a>
        </div>

        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>User</th>
                    <th>Session</th>
                    <th>Source</th>
                    <th>Action</th>
                    <th>Phase</th>
                    <th>Page</th>
                    <th>Method</th>
                    <th>Status</th>
                    <th>ms</th>
                    <th>Target</th>
                    <th>ID</th>
                    <th>Details</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows_html)}
            </tbody>
        </table>
    </body>
    </html>
    """