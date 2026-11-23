"""X (Twitter) via API v2 with an app bearer token.

Reading timelines needs a paid X API tier, and the user-timeline endpoint only
reaches the 3,200 most recent posts, so a backfill stops there.
"""
from __future__ import annotations

from datetime import datetime

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

API_URL = "https://api.x.com/2"
_KINDS = {"retweeted": PostKind.repost, "replied_to": PostKind.reply, "quoted": PostKind.quote}


class XClient:
    platform = Platform.x

    def __init__(self, bearer_token: str, *, transport: httpx.AsyncBaseTransport | None = None):
        if not bearer_token:
            raise PlatformNotConfigured("X API bearer token is not configured")
        self.token = bearer_token
        self._transport = transport

    async def _get(self, path: str, params: dict) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                base_url=API_URL,
                timeout=20,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self.token}"},
            ) as c:
                resp = await c.get(path, params=params)
        except httpx.HTTPError as exc:
            raise PlatformError(f"Could not reach X: {exc}", retryable=True) from exc
        if resp.status_code == 401:
            raise PlatformError("X rejected the API bearer token")
        return resp

    async def resolve(self, handle: str) -> ProfileInfo:
        username = handle.strip().lstrip("@")
        resp = await self._get(
            f"/users/by/username/{username}", {"user.fields": "name,profile_image_url"}
        )
        raise_for_status(resp, "X profile lookup")
        body = resp.json()
        data = body.get("data")
        if not data:
            raise ProfileNotFound(f"X account @{username} not found or suspended")
        return ProfileInfo(
            external_id=data["id"],
            handle=data["username"],
            display_name=data.get("name") or data["username"],
            avatar_url=data.get("profile_image_url"),
            profile_url=f"https://x.com/{data['username']}",
        )

    async def fetch_page(self, profile: ProfileInfo, cursor: str | None = None) -> Page:
        params = {
            "max_results": 100,
            "tweet.fields": "created_at,public_metrics,referenced_tweets,attachments,note_tweet",
            "expansions": "attachments.media_keys",
            "media.fields": "url,preview_image_url,type",
        }
        if cursor:
            params["pagination_token"] = cursor
        resp = await self._get(f"/users/{profile.external_id}/tweets", params)
        raise_for_status(resp, "X timeline")
        body = resp.json()
        media_by_key = {
            m["media_key"]: m.get("url") or m.get("preview_image_url")
            for m in (body.get("includes") or {}).get("media", [])
        }
        posts = [self._to_post(t, profile, media_by_key) for t in body.get("data", [])]
        return Page(posts=posts, next_cursor=(body.get("meta") or {}).get("next_token"))

    @staticmethod
    def _to_post(tweet: dict, profile: ProfileInfo, media_by_key: dict) -> FetchedPost:
        kind = PostKind.post
        for ref in tweet.get("referenced_tweets") or []:
            kind = _KINDS.get(ref.get("type"), kind)
        keys = (tweet.get("attachments") or {}).get("media_keys", [])
        metrics = tweet.get("public_metrics") or {}
        text = (tweet.get("note_tweet") or {}).get("text") or tweet.get("text", "")
        return FetchedPost(
            external_id=tweet["id"],
            posted_at=datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00")),
            text=text,
            kind=kind,
            url=f"https://x.com/{profile.handle}/status/{tweet['id']}",
            media=[media_by_key[k] for k in keys if media_by_key.get(k)],
            metrics={
                "likes": metrics.get("like_count", 0),
                "reposts": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "quotes": metrics.get("quote_count", 0),
            },
            raw=tweet,
        )
