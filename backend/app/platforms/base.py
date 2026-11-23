"""The contract every platform client implements, and the shared data types.

A client resolves a handle to a profile and pages through an account's posts
**newest first**. `fetch_page(cursor=None)` returns the newest page; passing the
returned `next_cursor` walks back in time. The scanner uses the first page(s)
for incremental scans and the cursor chain for history backfill.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.models.platform import Platform
from app.models.post import PostKind


class PlatformError(Exception):
    """A platform call failed. `retryable` errors are retried on the next run."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class PlatformNotConfigured(PlatformError):
    """Credentials for this platform are missing (see System settings)."""


class ProfileNotFound(PlatformError):
    """The handle doesn't exist, or the account is suspended/private."""


@dataclass
class ProfileInfo:
    external_id: str
    handle: str
    display_name: str = ""
    avatar_url: str | None = None
    profile_url: str | None = None


@dataclass
class FetchedPost:
    external_id: str
    posted_at: datetime
    text: str = ""
    kind: PostKind = PostKind.post
    url: str | None = None
    media: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Page:
    posts: list[FetchedPost]
    # None when there is nothing older.
    next_cursor: str | None = None


class PlatformClient(Protocol):
    platform: Platform

    async def resolve(self, handle: str) -> ProfileInfo: ...

    async def fetch_page(self, profile: ProfileInfo, cursor: str | None = None) -> Page: ...


def raise_for_status(resp: Any, what: str) -> None:
    """Map an httpx response to PlatformError (429 and 5xx are retryable)."""
    if resp.status_code < 400:
        return
    retryable = resp.status_code == 429 or resp.status_code >= 500
    raise PlatformError(f"{what} failed ({resp.status_code})", retryable=retryable)
