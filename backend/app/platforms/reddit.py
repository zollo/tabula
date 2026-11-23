"""Reddit via the OAuth API with an app-only (client credentials) token.

Reddit listings only reach back about 1,000 items per user, so a backfill
stops there.
"""
from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.models.platform import Platform
from app.models.post import PostKind
from app.platforms.base import (
    FetchedPost,
    Page,
    PlatformError,
    PlatformNotConfigured,
    ProfileInfo,
    ProfileNotFound,
    raise_for_status,
)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_URL = "https://oauth.reddit.com"


class RedditClient:
    platform = Platform.reddit

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not (client_id and client_secret):
            raise PlatformNotConfigured("Reddit API credentials are not configured")
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_agent = user_agent
        self._transport = transport
        self._token: str | None = None

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        headers = {"User-Agent": self.user_agent}
        try:
            async with httpx.AsyncClient(timeout=20, transport=self._transport) as c:
                if self._token is None:
                    tok = await c.post(
                        TOKEN_URL,
                        data={"grant_type": "client_credentials"},
                        auth=(self.client_id, self.client_secret),
                        headers=headers,
                    )
                    if tok.status_code in (400, 401):
                        raise PlatformError("Reddit rejected the API credentials")
                    raise_for_status(tok, "Reddit token request")
                    self._token = tok.json()["access_token"]
                headers["Authorization"] = f"Bearer {self._token}"
                return await c.get(f"{API_URL}{path}", params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise PlatformError(f"Could not reach Reddit: {exc}", retryable=True) from exc

    async def resolve(self, handle: str) -> ProfileInfo:
        name = handle.strip().removeprefix("/").removeprefix("u/").removeprefix("@")
        resp = await self._get(f"/user/{name}/about", {"raw_json": 1})
        if resp.status_code in (403, 404):
            raise ProfileNotFound(f"Reddit user {name!r} not found or suspended")
        raise_for_status(resp, "Reddit profile lookup")
        data = resp.json().get("data") or {}
        if not data.get("id"):
            raise ProfileNotFound(f"Reddit user {name!r} not found or suspended")
        icon = (data.get("icon_img") or "").split("?")[0] or None
        return ProfileInfo(
            external_id=f"t2_{data['id']}",
            handle=data["name"],
            display_name=(data.get("subreddit") or {}).get("title") or data["name"],
            avatar_url=icon,
            profile_url=f"https://www.reddit.com/user/{data['name']}",
        )

    async def fetch_page(self, profile: ProfileInfo, cursor: str | None = None) -> Page:
        params: dict = {"limit": 100, "raw_json": 1, "sort": "new"}
        if cursor:
            params["after"] = cursor
        resp = await self._get(f"/user/{profile.handle}/overview", params)
        raise_for_status(resp, "Reddit listing")
        data = resp.json().get("data") or {}
        posts = [self._to_post(child) for child in data.get("children", [])]
        return Page(posts=posts, next_cursor=data.get("after") or None)

    @staticmethod
    def _to_post(child: dict) -> FetchedPost:
        d = child["data"]
        is_comment = child.get("kind") == "t1"
        if is_comment:
            text = d.get("body", "")
        else:
            text = d.get("title", "")
            if d.get("selftext"):
                text = f"{text}\n\n{d['selftext']}"
        media: list[str] = []
        if not is_comment and d.get("post_hint") == "image" and d.get("url"):
            media.append(d["url"])
        return FetchedPost(
            external_id=d["name"],  # t1_… / t3_…
            posted_at=datetime.fromtimestamp(d["created_utc"], tz=UTC),
            text=text,
            kind=PostKind.comment if is_comment else PostKind.post,
            url=f"https://www.reddit.com{d['permalink']}" if d.get("permalink") else None,
            media=media,
            metrics={
                "score": d.get("score", 0),
                "comments": d.get("num_comments", 0),
                "subreddit": d.get("subreddit_name_prefixed"),
            },
            raw=child,
        )
