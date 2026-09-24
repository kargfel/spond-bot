"""
/api/v1/accounts — Dashboard user account management (admin only).

POST   /accounts              Create a dashboard user account
GET    /accounts              List all dashboard user accounts
PATCH  /accounts/{id}         Update role, linked Spond user, or password
DELETE /accounts/{id}         Delete a dashboard user account
"""
import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, DbDep
from app.core.security import hash_password
from app.models.frontend_user import FrontendUser
from app.schemas.auth import FrontendUserCreate, FrontendUserResponse, FrontendUserUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/accounts", tags=["Accounts"])


@router.post(
    "",
    response_model=FrontendUserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminDep],
    summary="Create a dashboard user account (admin only)",
)
async def create_account(payload: FrontendUserCreate, db: AsyncSession = DbDep):
    existing = await db.execute(
        select(FrontendUser).where(FrontendUser.username == payload.username)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username {payload.username!r} already exists.",
        )
    user = FrontendUser(
        id=uuid.uuid4(),
        username=payload.username,
        hashed_password=hash_password(payload.password),
        is_admin=payload.is_admin,
        linked_user_id=payload.linked_user_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("Created account %r (admin=%s).", user.username, user.is_admin)
    return user


@router.get(
    "",
    response_model=list[FrontendUserResponse],
    dependencies=[AdminDep],
    summary="List all dashboard user accounts (admin only)",
)
async def list_accounts(db: AsyncSession = DbDep):
    result = await db.execute(select(FrontendUser).order_by(FrontendUser.username))
    return result.scalars().all()


@router.patch(
    "/{account_id}",
    response_model=FrontendUserResponse,
    dependencies=[AdminDep],
    summary="Update a dashboard account's role, link, or password (admin only)",
)
async def update_account(
    account_id: uuid.UUID,
    payload: FrontendUserUpdate,
    db: AsyncSession = DbDep,
):
    user = await db.get(FrontendUser, account_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    if payload.is_admin is not None:
        user.is_admin = payload.is_admin
    if payload.linked_user_id is not None:
        user.linked_user_id = payload.linked_user_id
    if payload.new_password:
        user.hashed_password = hash_password(payload.new_password)
        logger.info("Admin reset password for account %r.", user.username)
    await db.commit()
    await db.refresh(user)
    logger.info("Updated account %r (is_admin=%s).", user.username, user.is_admin)
    return user


@router.delete(
    "/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[AdminDep],
    summary="Delete a dashboard user account (admin only)",
)
async def delete_account(account_id: uuid.UUID, db: AsyncSession = DbDep):
    user = await db.get(FrontendUser, account_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    await db.delete(user)
    await db.commit()
