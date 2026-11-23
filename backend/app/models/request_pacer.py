"""Shared request pacing across workers: one row per rate-limited endpoint."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RequestPacer(Base):
    __tablename__ = "request_pacers"

    # e.g. "reddit-public"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    # The earliest time the next request may start. Each request claims the
    # slot and pushes this forward by the interval (see pacer_service).
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A pause the site asked for (429, spent window, block). Requests that had
    # already reserved a slot re-check it before they send.
    hold_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
