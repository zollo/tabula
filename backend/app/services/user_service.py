"""User lookups, authentication and account management."""
from __future__ import annotations

import uuid

import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, Invalid, NotFound
from app.core.security import decode_access_token, hash_password, verify_password
from app.models.user import User, UserRole


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    user = await get_by_email(db, email)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password: str | None,
    display_name: str = "",
    role: UserRole = UserRole.user,
    is_enabled: bool = False,
    google_sub: str | None = None,
) -> User:
    if await get_by_email(db, email):
        raise Conflict("Email already registered")
    user = User(
        email=email.lower(),
        display_name=display_name or email.split("@")[0],
        hashed_password=hash_password(password) if password else None,
        role=role,
        is_enabled=is_enabled or role == UserRole.admin,
        google_sub=google_sub,
        google_email=email.lower() if google_sub else None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def resolve_token(db: AsyncSession, token: str) -> User | None:
    """Return the active user behind a session JWT."""
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(str(payload.get("sub")))
    except (jwt.PyJWTError, ValueError):
        return None
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


async def _active_admins(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(User).where(
            User.role == UserRole.admin, User.is_active.is_(True)
        )
    )
    return int(result.scalar_one())


async def update_user(db: AsyncSession, actor: User, user_id: uuid.UUID, changes: dict) -> User:
    """Admin edits: name, active, enabled, role, password."""
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    if user.id == actor.id and (
        changes.get("is_active") is False or changes.get("role") == UserRole.user
    ):
        raise Invalid("You can't deactivate or demote yourself")
    demoting = user.is_admin and (
        changes.get("is_active") is False or changes.get("role") == UserRole.user
    )
    if demoting and await _active_admins(db) <= 1:
        raise Invalid("Tabula needs at least one active admin")
    for key in ("display_name", "is_active", "is_enabled", "role"):
        if changes.get(key) is not None:
            setattr(user, key, changes[key])
    if changes.get("password"):
        user.hashed_password = hash_password(changes["password"])
    await db.commit()
    await db.refresh(user)
    return user


async def update_me(
    db: AsyncSession,
    user: User,
    *,
    display_name: str | None,
    current_password: str | None,
    new_password: str | None,
) -> User:
    if display_name is not None:
        user.display_name = display_name
    if new_password:
        if user.has_password and not verify_password(current_password or "", user.hashed_password):
            raise Invalid("Current password is incorrect")
        user.hashed_password = hash_password(new_password)
    await db.commit()
    await db.refresh(user)
    return user


async def delete_user(db: AsyncSession, actor: User, user_id: uuid.UUID) -> None:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    if user.id == actor.id:
        raise Invalid("You can't delete yourself")
    if user.is_admin and user.is_active and await _active_admins(db) <= 1:
        raise Invalid("Tabula needs at least one active admin")
    await db.delete(user)
    await db.commit()
