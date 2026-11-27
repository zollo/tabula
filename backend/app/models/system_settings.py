"""Admin-managed system settings: a single row (id = 1)."""
from __future__ import annotations

from sqlalchemy import Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import JSONType, TimestampMixin

SINGLETON_ID = 1


class SystemSettings(TimestampMixin, Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=SINGLETON_ID)
    allow_registration: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Platforms users may add (values of models.platform.Platform).
    enabled_platforms: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    min_scan_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    default_scan_interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60
    )
    # 0 = unlimited. Admins are never limited.
    max_trackings_per_user: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    # Stop backfilling an account after this many posts (0 = as far back as allowed).
    backfill_max_posts: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    # Platform credentials overriding the env vars, Fernet-encrypted
    # ({"x_bearer_token": "<token>", ...}). Never returned by the API.
    credentials: Mapped[dict[str, str]] = mapped_column(JSONType, nullable=False, default=dict)
    # Where each platform's posts come from: "api" (default), "browser"
    # ({"x": "browser"}; X and Reddit only), "public" (Reddit only: its
    # public .json pages, no login or credentials) or "archive" (Reddit only:
    # the Arctic Shift archive).
    platform_sources: Mapped[dict[str, str]] = mapped_column(
        JSONType, nullable=False, default=dict
    )
    # Browser collection pacing, per saved login: minimum seconds between page
    # loads, and page loads per UTC day.
    browser_page_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20
    )
    browser_daily_page_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=400)
    # Public (no-login) collection pacing: minimum seconds between requests,
    # shared by every worker (Reddit allows about 10 a minute without OAuth).
    public_request_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10
    )
