"""User management (admins only)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.db import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRead], operation_id="list_users")
async def list_users(
    _: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> list[User]:
    """Every account. Users waiting to be enabled come first."""
    result = await db.execute(
        select(User).order_by(User.is_enabled, User.role, User.display_name)
    )
    return list(result.scalars())


@router.post(
    "", response_model=UserRead, status_code=status.HTTP_201_CREATED, operation_id="create_user"
)
async def create_user(
    payload: UserCreate,
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> User:
    return await user_service.create_user(
        db,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        role=payload.role,
        is_enabled=payload.is_enabled,
    )


@router.patch("/{user_id}", response_model=UserRead, operation_id="update_user")
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    actor: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Rename, (de)activate, enable tracking, change role or set a password."""
    changes = payload.model_dump(exclude_unset=True)
    return await user_service.update_user(db, actor, user_id, changes)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="delete_user")
async def delete_user(
    user_id: uuid.UUID,
    actor: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete an account and its trackings (archived posts are kept)."""
    await user_service.delete_user(db, actor, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
