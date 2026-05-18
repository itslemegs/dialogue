from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.security import current_user  # adjust to your actual import
from app.services.lookup_ai import LookupRequest, run_lookup

router = APIRouter()

@router.post("/ai/lookup")
async def ai_lookup(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    payload = await request.json()
    req = LookupRequest.model_validate(payload)

    report = await run_lookup(req)
    return JSONResponse(report.model_dump())
