"""Bluesky via the public AppView (no credentials needed)."""
from __future__ import annotations

from datetime import datetime

import httpx

from app.models.platform import Platform
from app.models.post import PostKind
from app.platforms.base import (
    FetchedPost,
    Page,
    PlatformError,
    ProfileInfo,
    ProfileNotFound,
    raise_for_status,
)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class BlueskyClient:
    platform = Platform.bluesky

    def __init__(self, appview_url: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.base = appview_url.rstrip("/")
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base, timeout=20, transport=self._transport)

    async def resolve(self, handle: str) -> ProfileInfo:
        handle = handle.lstrip("@").strip()
        try:
            async with self._client() as c:
                resp = await c.get("/xrpc/app.bsky.actor.getProfile", params={"actor": handle})
        except httpx.HTTPError as exc:
            raise PlatformError(f"Could not reach Bluesky: {exc}", retryable=True) from exc
        if resp.status_code == 400:
            raise ProfileNotFound(f"Bluesky account {handle!r} not found")
        raise_for_status(resp, "Bluesky profile lookup")
        data = resp.json()
        return ProfileInfo(
            external_id=data["did"],
            handle=data["handle"],
            display_name=data.get("displayName") or data["handle"],
            avatar_url=data.get("avatar"),
            profile_url=f"https://bsky.app/profile/{data['handle']}",
        )

    async def fetch_page(self, profile: ProfileInfo, cursor: str | None = None) -> Page:
        params = {"actor": profile.external_id, "limit": 100, "filter": "posts_with_replies"}
        if cursor:
            params["cursor"] = cursor
        try:
            async with self._client() as c:
                resp = await c.get("/xrpc/app.bsky.feed.getAuthorFeed", params=params)
        except httpx.HTTPError as exc:
            raise PlatformError(f"Could not reach Bluesky: {exc}", retryable=True) from exc
        raise_for_status(resp, "Bluesky feed")
        data = resp.json()
        posts = [self._to_post(item) for item in data.get("feed", [])]
        return Page(posts=posts, next_cursor=data.get("cursor") or None)

    @staticmethod
    def _to_post(item: dict) -> FetchedPost:
        post = item["post"]
        record = post.get("record") or {}
        author = post.get("author") or {}
        rkey = post["uri"].rsplit("/", 1)[-1]
        reason = item.get("reason") or {}
        is_repost = reason.get("$type", "").endswith("reasonRepost")
        embed = post.get("embed") or {}
        if is_repost:
            kind = PostKind.repost
        elif record.get("reply"):
            kind = PostKind.reply
        elif embed.get("$type", "").startswith("app.bsky.embed.record"):
            kind = PostKind.quote
        else:
            kind = PostKind.post
        images = embed.get("images") or (embed.get("media") or {}).get("images") or []
        media = [i.get("fullsize") for i in images if i.get("fullsize")]
        posted = reason.get("indexedAt") if is_repost else record.get("createdAt")
        return FetchedPost(
            # A repost has the original's URI; prefix it so it can't collide.
            external_id=f"repost:{post['uri']}" if is_repost else post["uri"],
            posted_at=_parse_time(posted or post["indexedAt"]),
            text=record.get("text", ""),
            kind=kind,
            url=f"https://bsky.app/profile/{author.get('handle', author.get('did'))}/post/{rkey}",
            media=media,
            metrics={
                "likes": post.get("likeCount", 0),
                "reposts": post.get("repostCount", 0),
                "replies": post.get("replyCount", 0),
                "quotes": post.get("quoteCount", 0),
            },
            raw=item,
        )
