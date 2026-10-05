"""Helpers for the audit tests: real logins and real session cookies (no auth overrides),
so the audit middleware sees exactly what it sees in production."""
import uuid
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.jwt import create_access_token
from app.core.security import hash_password, password_version
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.frontend_user import FrontendUser

PASSWORD = "correct-horse-battery"


async def make_login(db, username="felix", *, admin=False, linked_user_id=None, password=PASSWORD) -> FrontendUser:
    user = FrontendUser(
        id=uuid.uuid4(), username=username, hashed_password=hash_password(password),
        is_admin=admin, linked_user_id=linked_user_id,
    )
    db.add(user)
    await db.commit()
    return user


def cookie_for(user: FrontendUser) -> str:
    return create_access_token({
        "sub": str(user.id), "username": user.username, "is_admin": user.is_admin,
        "linked_user_id": str(user.linked_user_id) if user.linked_user_id else None,
        "pwv": password_version(user.hashed_password),
    })


def client_factory(db):
    """`async with client(user) as c:` — signed in as `user` (or anonymous for None)."""
    from app.main import app

    @asynccontextmanager
    async def make(user=None, *, ip="203.0.113.7", user_agent="TestAgent/1.0"):
        async def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        deps_cookie = {"sb_session": cookie_for(user)} if user else {}
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app, client=(ip, 5000)), base_url="http://test",
                cookies=deps_cookie, headers={"user-agent": user_agent},
            ) as c:
                yield c
        finally:
            app.dependency_overrides.clear()

    return make


async def rows(db, *, action=None) -> list[AuditLog]:
    q = select(AuditLog).order_by(AuditLog.occurred_at, AuditLog.id).execution_options(populate_existing=True)
    if action:
        q = q.where(AuditLog.action == action)
    return list((await db.execute(q)).scalars().all())


async def only(db, action) -> AuditLog:
    found = await rows(db, action=action)
    assert len(found) == 1, f"expected exactly one {action!r}, got {[r.action for r in await rows(db)]}"
    return found[0]
