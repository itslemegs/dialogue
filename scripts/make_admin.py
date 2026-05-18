# scripts/make_admin.py
from sqlmodel import select
from app.db import get_session, seed_roles, grant_role
from app.models import User

EMAIL = "admin@admin"  # <-- change this

with get_session() as db:
    seed_roles()
    u = db.exec(select(User).where(User.email == EMAIL)).first()
    if not u:
        raise SystemExit("User not found. Register first.")
    grant_role(db, u, "admin")
    print("✅ Granted admin to:", u.handle, f"(id={u.id})")