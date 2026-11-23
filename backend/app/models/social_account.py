"""A social media account on some platform, shared by everyone who tracks it.

Posts belong to the account, not to a user, so two users tracking the same
account share one archive and one scan schedule (the shortest interval wins).
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin, UUIDMixin, str_enum
from app.models.platform import Platform

if TYPE_CHECKING:
    from app.models.tracking import Tracking


class BackfillStatus(enum.StrEnum):
    pending = "pending"      # queued, not started
    running = "running"      # paging back through history
    complete = "complete"    # reached the oldest post the platform will return
    failed = "failed"        # stopped on a non-retryable error (see last_error)


class SocialAccount(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_social_account"),)

    platform: Mapped[Platform] = mapped_column(str_enum(Platform, "platform"), nullable=False)
    # The platform's stable id (X user id, Bluesky DID, Reddit t2_ id).
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Handle as last seen (lowercased for lookups; handles can change).
    handle: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    profile_url: Mapped[str | None] = mapped_column(String(1024))

    # Scheduling. `scan_interval_minutes` is the shortest interval among active
    # trackings (None = nobody is tracking it, so it is not scanned).
    scan_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    # History backfill, resumable from `backfill_cursor`.
    backfill_status: Mapped[BackfillStatus] = mapped_column(
        str_enum(BackfillStatus, "backfill_status"),
        default=BackfillStatus.pending,
        nullable=False,
    )
    backfill_cursor: Mapped[str | None] = mapped_column(String(1024))
    backfill_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backfill_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    post_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    trackings: Mapped[list["Tracking"]] = relationship(
        back_populates="social_account", cascade="all, delete-orphan", lazy="raise"
    )
