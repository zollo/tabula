"""Reddit with no login at all: the public `.json` views of profile pages.

`https://www.reddit.com/user/<name>/about.json` and `overview.json` (posts and
comments, newest first) are what the site itself serves to signed-out
visitors, so this needs no API app, token or account. They share the API's
listing format and ~1,000-item history limit, so parsing reuses reddit.py.

Reddit allows about 10 unauthenticated requests a minute per IP. Every request
first waits for a slot from a `Pacer` shared by all workers, and the client
honours Reddit's own `x-ratelimit-*` headers and 429s by holding the pacer, so
nobody sends again until Reddit's window resets.
"""
from __future__ import annotations

from typing import Protocol
from urllib.parse import quote

import httpx

from app.models.platform import Platform
from app.platforms.base import Page, PlatformError, ProfileInfo, ProfileNotFound
from app.platforms.reddit import handle_from, page_from_listing, profile_from_about

BASE_URL = "https://www.reddit.com"
# Pause everyone this long after a 429 without a usable reset header.
RATE_LIMIT_HOLD_SECONDS = 120
# Pause everyone this long when Reddit blocks the server's IP (HTML 403).
BLOCKED_HOLD_SECONDS = 15 * 60
# Stop just before Reddit's window runs out rather than earn a 429.
LOW_REMAINING = 2


class Pacer(Protocol):
    async def wait(self) -> None: ...

    async def hold(self, seconds: float): ...


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class RedditPublicClient:
    platform = Platform.reddit

    def __init__(
        self,
        pacer: Pacer,
        user_agent: str,
        *,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.pacer = pacer
        # Reddit throttles generic agents hardest; a descriptive one is expected.
        self.user_agent = user_agent or "tabula/1.0 (archiver)"
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    async def _get(self, path: str, params: dict) -> tuple[int, dict | None]:
        """(status, JSON body or None). Raises retryable errors for throttling."""
        await self.pacer.wait()
        try:
            async with httpx.AsyncClient(
                timeout=20, transport=self._transport, follow_redirects=True
            ) as c:
                resp = await c.get(
                    f"{self.base_url}{path}",
                    params=params,
                    headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise PlatformError(f"Could not reach Reddit: {exc}", retryable=True) from exc

        reset = _float(resp.headers.get("x-ratelimit-reset"))
        if resp.status_code == 429:
            wait = _float(resp.headers.get("retry-after")) or reset or RATE_LIMIT_HOLD_SECONDS
            await self.pacer.hold(max(wait, 10))
            raise PlatformError("Reddit rate-limited public requests", retryable=True)
        remaining = _float(resp.headers.get("x-ratelimit-remaining"))
        if remaining is not None and remaining < LOW_REMAINING and reset:
            await self.pacer.hold(reset)

        body = None
        if "json" in resp.headers.get("content-type", ""):
            try:
                body = resp.json()
            except ValueError:
                body = None
        if not isinstance(body, dict):
            if resp.status_code == 403:
                # An HTML "blocked" page: Reddit refuses this IP or agent for now.
                await self.pacer.hold(BLOCKED_HOLD_SECONDS)
                raise PlatformError(
                    "Reddit blocked public requests from this server (403)", retryable=True
                )
            raise PlatformError(
                f"Reddit didn't return data (status {resp.status_code})",
                retryable=resp.status_code >= 500 or resp.status_code < 400,
            )
        if resp.status_code >= 500:
            raise PlatformError(f"Reddit returned status {resp.status_code}", retryable=True)
        return resp.status_code, body

    async def resolve(self, handle: str) -> ProfileInfo:
        name = handle_from(handle)
        status, body = await self._get(f"/user/{quote(name)}/about.json", {"raw_json": 1})
        if status in (403, 404):
            raise ProfileNotFound(f"Reddit user {name!r} not found or suspended")
        if status >= 400:
            raise PlatformError(f"Reddit profile lookup failed ({status})")
        if body.get("kind") != "t2":
            raise PlatformError("Reddit returned an unexpected profile response", retryable=True)
        profile = profile_from_about(body)
        if profile is None:
            raise ProfileNotFound(f"Reddit user {name!r} not found or suspended")
        return profile

    async def fetch_page(self, profile: ProfileInfo, cursor: str | None = None) -> Page:
        params: dict = {"limit": 100, "raw_json": 1, "sort": "new"}
        if cursor:
            params["after"] = cursor
        status, body = await self._get(f"/user/{quote(profile.handle)}/overview.json", params)
        if status in (403, 404):
            raise ProfileNotFound(f"Reddit user {profile.handle!r} is gone or suspended")
        if status >= 400:
            raise PlatformError(f"Reddit listing failed ({status})")
        # Only a real listing may end a backfill; anything else is retried.
        data = body.get("data") if body.get("kind") == "Listing" else None
        if not isinstance(data, dict) or not isinstance(data.get("children"), list):
            raise PlatformError("Reddit returned an unexpected listing response", retryable=True)
        return page_from_listing(body)
