# app/security.py
import os
import secrets
import hashlib
from typing import Optional

from passlib.hash import bcrypt
from itsdangerous import TimestampSigner, BadSignature

# -------------------------------
# Secret & signer
# -------------------------------
# IMPORTANT: set CONSENSUS_SECRET in your environment in prod
SECRET = os.environ.get("CONSENSUS_SECRET") or secrets.token_hex(32)
signer = TimestampSigner(SECRET)

# -------------------------------
# Password hashing
# -------------------------------
def hash_password(pw: str) -> str:
    return bcrypt.hash(pw)

def verify_password(pw: str, pw_hash: str) -> bool:
    return bcrypt.verify(pw, pw_hash)

# -------------------------------
# Session cookie helpers
# -------------------------------
def sign_cookie_value(value: str) -> str:
    signed = signer.sign(value)
    return signed.decode() if isinstance(signed, (bytes, bytearray)) else str(signed)

def unsign_cookie(signed: Optional[str], max_age: int = 60 * 60 * 24 * 7) -> Optional[int]:
    """Verify signed cookie and return user_id (int) if valid; else None."""
    if not signed:  # handles None and empty string
        return None
    try:
        raw = signer.unsign(signed, max_age=max_age)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        return int(raw)
    except (BadSignature, ValueError):
        # BadSignature includes SignatureExpired; ValueError handles non-int payloads
        return None

# def login_user(resp, user_id: int, cookie_name: str = "session"):
#     signed = sign_cookie_value(str(int(user_id)))
#     resp.set_cookie(
#         cookie_name,
#         signed,
#         httponly=True,
#         samesite="lax",
#         secure=False,  # True behind HTTPS
#         path="/",
#         max_age=60 * 60 * 24 * 7,
#     )
#     return resp

# security.py
from typing import Optional
import os

SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
IS_PROD = os.getenv("ENV", "dev") == "prod"  # or however you detect prod

def login_user(
    resp,
    user_id: int,
    cookie_name: Optional[str] = None,  # allow auto-switching to __Host- in prod
    samesite: Optional[str] = None,
):
    if cookie_name is None:
        cookie_name = "__Host-session" if IS_PROD else "session"

    if samesite is None:
        # Change to "none" ONLY if you truly need cross-site (embeds, OAuth across domains)
        samesite = "lax"

    signed = sign_cookie_value(str(int(user_id)))

    resp.set_cookie(
        key=cookie_name,
        value=signed,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=IS_PROD,   # MUST be True if samesite == "none"
        samesite=samesite,
        path="/",
        # NOTE: __Host- cookies must NOT set a domain
        # domain="your.app.tld"  # set only if you really need it (and not for __Host-)
    )
    return resp

def logout_user(resp, cookie_name: Optional[str] = None):
    if cookie_name is None:
        cookie_name = "__Host-session" if IS_PROD else "session"
    # Must match path/domain used above
    resp.delete_cookie(key=cookie_name, path="/")
    return resp

# Optional utility retained if you need random tokens elsewhere
def new_session_token() -> str:
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()

# -------------------------------
# Role helpers
# -------------------------------
def has_role(user, name: str) -> bool:
    return any(getattr(r, "name", None) == name for r in getattr(user, "roles", []) or [])

# def effective_flags(user):
#     is_admin = has_role(user, "admin")
#     is_president = has_role(user, "president")
#     is_chair = is_president or has_role(user, "chairman")
#     is_invited = has_role(user, "invited speaker")
#     is_member = has_role(user, "member") or is_admin or is_chair or is_invited
#     return {
#         "IS_ADMIN": is_admin,
#         "IS_PRESIDENT": is_president,
#         "IS_CHAIR": is_chair,
#         "IS_INVITED": is_invited,
#         "IS_MEMBER": is_member,
#     }

# -------------------------------
# Optional RBAC dependency
# -------------------------------
from fastapi import Depends, HTTPException, status  # noqa: E402

def effective_flags(user):
    is_admin = has_role(user, "admin")
    is_president = has_role(user, "president")
    is_chair = is_president or has_role(user, "chairman")
    is_invited = has_role(user, "invited speaker")
    is_banned = has_role(user, "banned")
    is_member = has_role(user, "member") or is_admin or is_chair or is_invited
    return {
        "IS_ADMIN": is_admin,
        "IS_PRESIDENT": is_president,
        "IS_CHAIR": is_chair,
        "IS_INVITED": is_invited,
        "IS_MEMBER": is_member,
        "IS_BANNED": is_banned,
    }

from fastapi import Request, HTTPException
from app.db import get_session
from app.models import User
# from app.main import current_user

def current_user(request: Request, allow_banned: bool = False):
    if hasattr(request.state, "user_cached") and allow_banned:
        # Only reuse when allow_banned=True or the cached user is not banned
        return request.state.user_cached

    uid = unsign_cookie(request.cookies.get("session"))
    if uid is None:
        user = None
    else:
        with get_session() as db:
            user = db.get(User, uid)
            if user and (not allow_banned) and has_role(user, "banned"):
                raise HTTPException(status_code=403, detail="Your account is banned")

    # cache for the rest of the request
    request.state.user_cached = user
    return user

def role_required(*required: str):
    def dep(user = Depends(current_user)):  # noqa: F821
        flags = effective_flags(user)
        if flags["IS_BANNED"]:
            raise HTTPException(status_code=403, detail="Banned users cannot access this resource")
        granted = {name for name, val in {
            "admin": flags["IS_ADMIN"],
            "president": flags["IS_PRESIDENT"],
            "chairman": flags["IS_CHAIR"],
            "member": flags["IS_MEMBER"],
        }.items() if val}
        if not (granted & set(required)):
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user
    return dep