"""Reddit through a browser, signed in or not.

Loads Reddit's public `.json` views (`/user/<name>/about.json`, `overview.json`)
in the browser and parses them with the same code as the API client. Listings
still stop at about 1,000 items: that's a Reddit limit, not an API one.
"""
from __future__ import annotations

from urllib.parse import quote

from app.models.platform import Platform
from app.platforms.base import Page, ProfileInfo, ProfileNotFound
from app.platforms.browser.base import BrowserClient
from app.platforms.browser.driver import Captured
from app.platforms.reddit import handle_from, page_from_listing, profile_from_about

BASE_URL = "https://www.reddit.com"


class RedditBrowserClient(BrowserClient):
    platform = Platform.reddit
    label = "Reddit"

    def _check(self, captured: Captured) -> dict | None:
        """The JSON body, or None for a 403/404 JSON answer (missing/suspended user)."""
        if "/login" in captured.final_url:
            raise self.fail("challenged", "Reddit signed this session out")
        if captured.status == 429:
            raise self.fail("rate_limited", "Reddit rate-limited this session")
        body = captured.bodies[0] if captured.bodies else None
        if captured.status in (403, 404) and isinstance(body, dict):
            return None
        if not isinstance(body, dict):
            # Reddit answers a blocked or throttled client with an HTML page.
            raise self.fail(
                "rate_limited", f"Reddit didn't return data (status {captured.status})"
            )
        if captured.status and captured.status >= 400:
            raise self.fail("error", f"Reddit returned status {captured.status}")
        return body

    async def resolve(self, handle: str) -> ProfileInfo:
        name = handle_from(handle)
        captured = await self._load(
            lambda d: d.get_json(f"{BASE_URL}/user/{quote(name)}/about.json?raw_json=1")
        )
        body = self._check(captured)
        profile = profile_from_about(body) if body else None
        if profile is None:
            raise ProfileNotFound(f"Reddit user {name!r} not found or suspended")
        return profile

    async def fetch_page(self, profile: ProfileInfo, cursor: str | None = None) -> Page:
        url = f"{BASE_URL}/user/{quote(profile.handle)}/overview.json?limit=100&raw_json=1&sort=new"
        if cursor:
            url += f"&after={quote(cursor)}"
        captured = await self._load(lambda d: d.get_json(url))
        body = self._check(captured)
        if body is None:
            raise ProfileNotFound(f"Reddit user {profile.handle!r} is gone or suspended")
        return page_from_listing(body)
