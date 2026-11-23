"""Tracked accounts: a user's subscriptions, the archive and exports."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core import export
from app.db import get_db
from app.models.post import PostKind
from app.models.social_account import SocialAccount
from app.models.tracking import Tracking
from app.models.user import User
from app.schemas.tracking import (
    PostPage,
    PostRead,
    SocialAccountRead,
    TrackingCreate,
    TrackingRead,
    TrackingUpdate,
)
from app.services import post_service, tracking_service

router = APIRouter(tags=["tracking"])


@router.get("/trackings", response_model=list[TrackingRead], operation_id="list_trackings")
async def list_trackings(
    all_users: bool = Query(False, description="Admins only: every user's trackings."),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Tracking]:
    return await tracking_service.list_trackings(db, user, all_users)


@router.post(
    "/trackings",
    response_model=TrackingRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_tracking",
)
async def create_tracking(
    payload: TrackingCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Tracking:
    """Start tracking an account. A new account's history is backfilled in the background."""
    return await tracking_service.add_tracking(
        db, user, payload.platform, payload.handle, payload.scan_interval_minutes
    )


@router.get("/trackings/{tracking_id}", response_model=TrackingRead, operation_id="get_tracking")
async def get_tracking(
    tracking_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Tracking:
    return await tracking_service.get_tracking(db, user, tracking_id)


@router.patch(
    "/trackings/{tracking_id}", response_model=TrackingRead, operation_id="update_tracking"
)
async def update_tracking(
    tracking_id: uuid.UUID,
    payload: TrackingUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Tracking:
    """Change the scan frequency, or pause/resume scanning."""
    return await tracking_service.update_tracking(
        db, user, tracking_id, payload.scan_interval_minutes, payload.is_paused
    )


@router.delete(
    "/trackings/{tracking_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_tracking",
)
async def delete_tracking(
    tracking_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stop tracking. The archive is kept and resumes if anyone tracks the account again."""
    await tracking_service.delete_tracking(db, user, tracking_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/trackings/{tracking_id}/scan",
    response_model=TrackingRead,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="scan_tracking_now",
)
async def scan_now(
    tracking_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Tracking:
    """Queue a scan right away instead of waiting for the schedule."""
    return await tracking_service.request_scan(db, user, tracking_id)


@router.get(
    "/accounts/{account_id}", response_model=SocialAccountRead, operation_id="get_account"
)
async def get_account(
    account_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SocialAccount:
    return await tracking_service.get_account(db, user, account_id)


@router.get(
    "/accounts/{account_id}/posts", response_model=PostPage, operation_id="list_posts"
)
async def list_posts(
    account_id: uuid.UUID,
    q: str | None = Query(None, max_length=200, description="Text search"),
    kind: PostKind | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PostPage:
    """Archived posts, newest first."""
    account = await tracking_service.get_account(db, user, account_id)
    items, total = await post_service.list_posts(
        db, account, q=q, kind=kind, since=since, until=until, limit=limit, offset=offset
    )
    return PostPage(total=total, items=[PostRead.model_validate(p) for p in items])


@router.get(
    "/accounts/{account_id}/export",
    operation_id="export_posts",
    responses={
        200: {
            "content": {"application/json": {}, "application/pdf": {}},
            "description": "The archive (with the same filters as list_posts) as a download.",
        }
    },
)
async def export_posts(
    account_id: uuid.UUID,
    format: Literal["json", "pdf"] = "json",
    q: str | None = Query(None, max_length=200),
    kind: PostKind | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    account = await tracking_service.get_account(db, user, account_id)
    posts = await post_service.all_posts(db, account, q=q, kind=kind, since=since, until=until)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"tabula-{account.platform.value}-{account.handle}")
    if format == "pdf":
        body, media_type = export.to_pdf(account, posts), "application/pdf"
    else:
        body = json.dumps(export.to_json(account, posts), indent=2, ensure_ascii=False).encode()
        media_type = "application/json"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{stem}.{format}"'},
    )
