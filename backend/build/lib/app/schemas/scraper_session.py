"""Browser session (saved login) schemas. The login itself is never returned."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.platform import Platform
from app.models.scraper_session import SessionStatus


class ScraperSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    platform: Platform
    label: str
    signed_in: bool
    status: SessionStatus
    last_error: str | None
    cooldown_until: datetime | None
    in_use: bool
    last_used_at: datetime | None
    pages_today: int
    pages_total: int
    created_at: datetime


class ScraperSessionCreate(BaseModel):
    platform: Platform
    label: str = Field(min_length=1, max_length=255)
    # Playwright storage-state JSON (from `python -m app.tools.capture_session`).
    # Omit for a signed-out Reddit session.
    storage_state: str | None = Field(default=None, max_length=2_000_000)


class ScraperSessionUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=255)
    # true re-activates (clearing errors and cooldowns); false turns it off.
    enabled: bool | None = None
    # Replace the saved login (re-activates it).
    storage_state: str | None = Field(default=None, max_length=2_000_000)
