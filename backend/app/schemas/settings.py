"""System settings and platform option schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.platform import Platform

CredentialSource = Literal["saved", "environment", "unset"]
Source = Literal["api", "browser", "public", "archive"]


class PlatformOption(BaseModel):
    value: Platform
    label: str
    enabled: bool
    # Credentials (or, for the browser source, a working session) are in
    # place, so adding accounts can work.
    configured: bool


class TrackingOptions(BaseModel):
    platforms: list[PlatformOption]
    min_scan_interval_minutes: int
    default_scan_interval_minutes: int
    max_trackings_per_user: int


class SystemSettingsRead(BaseModel):
    allow_registration: bool
    enabled_platforms: list[Platform]
    min_scan_interval_minutes: int
    default_scan_interval_minutes: int
    max_trackings_per_user: int
    backfill_max_posts: int
    # Where each platform credential comes from. Values are never returned.
    credentials: dict[str, CredentialSource]
    # Non-secret credential values in effect (e.g. Reddit user agent).
    credential_values: dict[str, str]
    # Where each platform's posts come from; only X and Reddit offer "browser",
    # only Reddit offers "public" (no login or credentials) and "archive"
    # (the Arctic Shift archive, no Reddit request at all).
    platform_sources: dict[Platform, Source]
    browser_platforms: list[Platform]
    public_platforms: list[Platform]
    archive_platforms: list[Platform]
    browser_page_interval_seconds: int
    browser_daily_page_cap: int
    public_request_interval_seconds: int


class SystemSettingsUpdate(BaseModel):
    allow_registration: bool | None = None
    enabled_platforms: list[Platform] | None = None
    min_scan_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    default_scan_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    max_trackings_per_user: int | None = Field(default=None, ge=0, le=100000)
    backfill_max_posts: int | None = Field(default=None, ge=0, le=10_000_000)
    platform_sources: dict[Platform, Source] | None = None
    # Must stay well under the session lease (scraper_session_service.LEASE_TTL).
    browser_page_interval_seconds: int | None = Field(default=None, ge=5, le=300)
    # 0 = no daily cap.
    browser_daily_page_cap: int | None = Field(default=None, ge=0, le=100_000)
    # Seconds between public (no-login) requests across all workers. Reddit
    # allows about 10 a minute without OAuth, hence the floor of 6.
    public_request_interval_seconds: int | None = Field(default=None, ge=6, le=600)
    # Set a value to save it (encrypted); "" or null clears the saved value so
    # the env var applies again. Omitted keys are left alone.
    credentials: dict[str, str | None] = Field(default_factory=dict)
