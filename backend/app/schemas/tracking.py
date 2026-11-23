"""Tracked accounts and archived posts."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.platform import Platform
from app.models.post import PostKind
from app.models.social_account import BackfillStatus


class SocialAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    platform: Platform
    handle: str
    display_name: str
    avatar_url: str | None
    profile_url: str | None
    scan_interval_minutes: int | None
    last_scanned_at: datetime | None
    next_scan_at: datetime | None
    last_error: str | None
    backfill_status: BackfillStatus
    backfill_finished_at: datetime | None
    post_count: int


class TrackingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    scan_interval_minutes: int
    is_paused: bool
    created_at: datetime
    social_account: SocialAccountRead


class TrackingCreate(BaseModel):
    platform: Platform
    # Handle (with or without @) or a profile URL.
    handle: str = Field(min_length=1, max_length=512)
    # Defaults to the system default; can't be below the system minimum.
    scan_interval_minutes: int | None = Field(default=None, ge=1, le=10080)


class TrackingUpdate(BaseModel):
    scan_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    is_paused: bool | None = None


class PostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_id: str
    kind: PostKind
    url: str | None
    text: str
    posted_at: datetime
    media: list[str]
    metrics: dict[str, Any]
    fetched_at: datetime


class PostPage(BaseModel):
    total: int
    items: list[PostRead]
