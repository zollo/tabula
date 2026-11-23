"""Browser sessions: saved logins for browser collection (admins only)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.db import get_db
from app.models.scraper_session import ScraperSession
from app.models.user import User
from app.schemas.scraper_session import (
    ScraperSessionCreate,
    ScraperSessionRead,
    ScraperSessionUpdate,
)
from app.services import scraper_session_service as svc

router = APIRouter(prefix="/browser-sessions", tags=["browser sessions"])


def _read(row: ScraperSession) -> ScraperSessionRead:
    now = datetime.now(UTC)
    leased = svc._aware(row.leased_until)
    today = now.strftime("%Y-%m-%d")
    return ScraperSessionRead(
        id=row.id,
        platform=row.platform,
        label=row.label,
        signed_in=bool(row.storage_state),
        status=row.status,
        last_error=row.last_error,
        cooldown_until=row.cooldown_until,
        in_use=bool(row.lease_token and leased and leased > now),
        last_used_at=row.last_used_at,
        pages_today=row.pages_today if row.pages_day == today else 0,
        pages_total=row.pages_total,
        created_at=row.created_at,
    )


@router.get("", response_model=list[ScraperSessionRead], operation_id="list_browser_sessions")
async def list_browser_sessions(
    _: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> list[ScraperSessionRead]:
    return [_read(r) for r in await svc.list_sessions(db)]


@router.post(
    "",
    response_model=ScraperSessionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_browser_session",
)
async def create_browser_session(
    payload: ScraperSessionCreate,
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> ScraperSessionRead:
    """Save a login captured with `python -m app.tools.capture_session`."""
    row = await svc.create_session(db, payload.platform, payload.label, payload.storage_state)
    return _read(row)


@router.patch(
    "/{session_id}", response_model=ScraperSessionRead, operation_id="update_browser_session"
)
async def update_browser_session(
    session_id: uuid.UUID,
    payload: ScraperSessionUpdate,
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> ScraperSessionRead:
    row = await svc.update_session(
        db,
        session_id,
        label=payload.label,
        enabled=payload.enabled,
        storage_state=payload.storage_state,
    )
    return _read(row)


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_browser_session",
)
async def delete_browser_session(
    session_id: uuid.UUID,
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await svc.delete_session(db, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
