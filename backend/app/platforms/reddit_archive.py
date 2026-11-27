"""Reddit from the Arctic Shift archive: no login, no Reddit request at all.

Reddit has refused signed-out `.json` requests (403) since mid-2026, so this reads
a user's posts and comments from Arctic Shift
(https://arctic-shift.photon-reddit.com), a free, community-run archive that
ingests Reddit within minutes. Its records are Reddit's own objects, so parsing
reuses reddit.py. Unlike Reddit's listings it isn't capped at ~1,000 items: a
backfill can reach an account's whole history.

Caveats: it's run by one person with no uptime guarantee, and scores are as of
ingest (plus a second pass about a day and a half later), so later edits and
deletions don't show up.

Posts and comments come from two endpoints, each paged backwards with
`before=<epoch seconds>` (exclusive). A page merges both and stops at the newer
of the two lists' oldest items when a list was full, so nothing is skipped; the
cursor is that boundary second, whose items start the next page.
"""
from __future__ import annotations

from typing import Protocol

import httpx

from app.models.platform import Platform
from app.platforms.base import Page, PlatformError, ProfileInfo, ProfileNotFound
from app.platforms.reddit import handle_from, to_post

BASE_URL = "https://arctic-shift.photon-reddit.com/api"
PAGE_SIZE = 100  # the API's maximum
# Arctic Shift publishes no limit; its author suggests about one request every
# two seconds. All workers share this pace.
REQUEST_INTERVAL_SECONDS = 2
# Pause everyone this long after a 429 without a usable reset header.
RATE_LIMIT_HOLD_SECONDS = 60


class Pacer(Protocol):
    async def wait(self) -> None: ...

    async def hold(self, seconds: float): ...


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class RedditArchiveClient:
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
        self.user_agent = user_agent or "tabula/1.0 (archiver)"
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    async def _search(self, kind: str, params: dict) -> list[dict]:
        """`kind` is "posts" or "comments": the matching records, newest first."""
        await self.pacer.wait()
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as c:
                resp = await c.get(
                    f"{self.base_url}/{kind}/search",
                    params={"sort": "desc", **params},
                    headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise PlatformError(f"Could not reach Arctic Shift: {exc}", retryable=True) from exc

        if resp.status_code == 429:
            wait = (
                _float(resp.headers.get("retry-after"))
                or _float(resp.headers.get("x-ratelimit-reset"))
                or RATE_LIMIT_HOLD_SECONDS
            )
            await self.pacer.hold(max(wait, 5))
            raise PlatformError("Arctic Shift rate-limited requests", retryable=True)
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise PlatformError(
                f"Arctic Shift didn't return data (status {resp.status_code})", retryable=True
            )
        if resp.status_code >= 400 or body.get("error"):
            # Its overload answer is an error like "Timeout. Maybe slow down a bit."
            # Treat every error as temporary: a bad request here is our bug, and
            # retrying beats ending a backfill early.
            raise PlatformError(
                f"Arctic Shift returned an error ({resp.status_code}): {body.get('error')}",
                retryable=True,
            )
        data = body.get("data")
        if not isinstance(data, list):
            raise PlatformError("Arctic Shift returned an unexpected response", retryable=True)
        return [d for d in data if isinstance(d, dict)]

    async def resolve(self, handle: str) -> ProfileInfo:
        name = handle_from(handle)
        latest = None
        for kind in ("posts", "comments"):
            found = await self._search(kind, {"author": name, "limit": 1})
            if found and found[0].get("author_fullname"):
                latest = found[0]
                break
        if latest is None:
            raise ProfileNotFound(f"Reddit user {name!r} has nothing in the archive")
        author = latest.get("author") or name
        return ProfileInfo(
            external_id=latest["author_fullname"],  # t2_…, as the API reports it
            handle=author,
            display_name=author,
            avatar_url=None,
            profile_url=f"https://www.reddit.com/user/{author}",
        )

    async def fetch_page(self, profile: ProfileInfo, cursor: str | None = None) -> Page:
        params: dict = {"author": profile.handle, "limit": PAGE_SIZE}
        if cursor:
            try:
                # The cursor is the boundary second; `before` is exclusive.
                params["before"] = int(cursor) + 1
            except ValueError:
                pass  # a cursor from another source: start from the newest
        posts = await self._search("posts", params)
        comments = await self._search("comments", params)

        # A full list may have more items older than its last one; stop at the
        # newest such cut-off so neither list skips anything.
        cutoffs = [
            int(items[-1]["created_utc"])
            for items in (posts, comments)
            if len(items) >= PAGE_SIZE and items[-1].get("created_utc") is not None
        ]
        boundary = max(cutoffs) if cutoffs else None
        records = [
            (kind, d)
            for kind, items in (("t3", posts), ("t1", comments))
            for d in items
            if d.get("created_utc") is not None
        ]
        next_cursor = None
        if boundary is not None:
            # Items from the boundary second itself go on the next page (which
            # reads that second inclusively), so pages never overlap and a scan
            # only stops at posts it really had before.
            newer = [r for r in records if int(r[1]["created_utc"]) > boundary]
            if newer:
                records, next_cursor = newer, str(boundary)
            else:
                # A whole page within one second: keep it and step past that
                # second rather than loop (vanishingly rare).
                records = [r for r in records if int(r[1]["created_utc"]) >= boundary]
                next_cursor = str(boundary - 1)
        records.sort(key=lambda r: r[1]["created_utc"], reverse=True)
        fetched = []
        for kind, d in records:
            if not d.get("name") and d.get("id"):
                d = {**d, "name": f"{kind}_{d['id']}"}
            if d.get("name"):
                fetched.append(to_post({"kind": kind, "data": d}))
        return Page(posts=fetched, next_cursor=next_cursor)
