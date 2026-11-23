"""A saved browser login used to collect posts from a site's web app.

Browser collection (X, Reddit) runs in the browser worker with one of these
sessions. A worker *leases* a session so two browsers never share a login, and
each session is paced (`next_allowed_at`) and capped per day. `storage_state`
is a Playwright storage-state JSON (cookies + local storage), Fernet-encrypted;
NULL means "signed out", which only Reddit allows.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDMixin, str_enum
from app.models.platform import Platform


class SessionStatus(enum.StrEnum):
    active = "active"                    # usable (subject to pacing and the daily cap)
    cooling_down = "cooling_down"        # rate-limited; usable again after cooldown_until
    needs_attention = "needs_attention"  # signed out or challenged; an admin must replace it
    disabled = "disabled"                # turned off by an admin


class ScraperSession(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "scraper_sessions"

    platform: Mapped[Platform] = mapped_column(str_enum(Platform, "platform"), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_state: Mapped[str | None] = mapped_column(Text)
    status: Mapped[SessionStatus] = mapped_column(
        str_enum(SessionStatus, "session_status"), nullable=False, default=SessionStatus.active
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Lease: held by one worker until leased_until (renewed after each page).
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    # Pacing: the session isn't used again before this.
    next_allowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Pages loaded on `pages_day` (UTC, YYYY-MM-DD), for the daily cap.
    pages_day: Mapped[str | None] = mapped_column(String(10))
    pages_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
