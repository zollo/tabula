"""System settings and platform option schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.platform import Platform

CredentialSource = Literal["saved", "environment", "unset"]


class PlatformOption(BaseModel):
    value: Platform
    label: str
    enabled: bool
    # Credentials are in place, so adding accounts can work.
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


class SystemSettingsUpdate(BaseModel):
    allow_registration: bool | None = None
    enabled_platforms: list[Platform] | None = None
    min_scan_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    default_scan_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    max_trackings_per_user: int | None = Field(default=None, ge=0, le=100000)
    backfill_max_posts: int | None = Field(default=None, ge=0, le=10_000_000)
    # Set a value to save it (encrypted); "" or null clears the saved value so
    # the env var applies again. Omitted keys are left alone.
    credentials: dict[str, str | None] = Field(default_factory=dict)
