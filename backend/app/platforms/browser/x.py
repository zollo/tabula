"""X (Twitter) through a signed-in browser session.

Reads the JSON that x.com's own web app loads:

* `resolve` opens the profile and reads the `UserByScreenName` GraphQL response.
* `fetch_page` runs a "Latest" search for `from:<handle>` and reads the
  `SearchTimeline` responses while scrolling. The cursor is a `max_id` (one
  below the oldest post seen), so each page walks further back in time and,
  unlike the profile timeline, isn't stopped at the newest ~3,200 posts.

X's internal response shapes change without notice, so the parsing below walks
the JSON for tweet objects instead of relying on exact paths.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.models.platform import Platform
from app.models.post import PostKind
from app.platforms.base import FetchedPost, Page, ProfileInfo, ProfileNotFound
from app.platforms.browser.base import BrowserClient
from app.platforms.browser.driver import Captured

BASE_URL = "https://x.com"
# Stop scrolling once this many posts are loaded, or after this many scrolls.
PAGE_TARGET = 40
MAX_SCROLLS = 8
# Being sent here means the login no longer works.
_LOGIN_MARKERS = ("/login", "/i/flow/", "/account/access", "/logout")


def _walk(node: Any, key: str) -> Iterator[Any]:
    """Every value stored under `key`, anywhere in a JSON document."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k == key:
                    yield v
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)


def _unwrap(result: Any) -> dict | None:
    if not isinstance(result, dict):
        return None
    if result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet") or {}
    return result if result.get("rest_id") and result.get("legacy") else None


def parse_user(bodies: list[Any]) -> ProfileInfo | None:
    for body in bodies:
        for user in _walk(body, "user"):
            result = (user or {}).get("result") if isinstance(user, dict) else None
            if not isinstance(result, dict) or result.get("__typename") != "User":
                continue
            legacy = result.get("legacy") or {}
            core = result.get("core") or {}
            handle = core.get("screen_name") or legacy.get("screen_name")
            if not (result.get("rest_id") and handle):
                continue
            avatar = (result.get("avatar") or {}).get("image_url") or legacy.get(
                "profile_image_url_https"
            )
            return ProfileInfo(
                external_id=result["rest_id"],
                handle=handle,
                display_name=core.get("name") or legacy.get("name") or handle,
                avatar_url=avatar,
                profile_url=f"{BASE_URL}/{handle}",
            )
    return None


def _author_id(tweet: dict) -> str | None:
    legacy = tweet.get("legacy") or {}
    user = ((tweet.get("core") or {}).get("user_results") or {}).get("result") or {}
    return legacy.get("user_id_str") or user.get("rest_id")


def _text(tweet: dict) -> str:
    note = (((tweet.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result")) or {}
    return note.get("text") or (tweet.get("legacy") or {}).get("full_text", "")


def to_post(tweet: dict, handle: str) -> FetchedPost:
    legacy = tweet["legacy"]
    kind = PostKind.post
    text = _text(tweet)
    original = _unwrap((legacy.get("retweeted_status_result") or {}).get("result"))
    if original is not None:
        kind = PostKind.repost
        text = _text(original)
    elif legacy.get("in_reply_to_status_id_str"):
        kind = PostKind.reply
    elif legacy.get("is_quote_status"):
        kind = PostKind.quote
    source = original or tweet
    media = [
        m["media_url_https"]
        for m in ((source.get("legacy") or {}).get("extended_entities") or {}).get("media", [])
        if m.get("media_url_https")
    ]
    views = (tweet.get("views") or {}).get("count")
    metrics: dict[str, Any] = {
        "likes": legacy.get("favorite_count", 0),
        "reposts": legacy.get("retweet_count", 0),
        "replies": legacy.get("reply_count", 0),
        "quotes": legacy.get("quote_count", 0),
    }
    if views is not None:
        metrics["views"] = int(views)
    return FetchedPost(
        external_id=tweet["rest_id"],
        posted_at=datetime.strptime(legacy["created_at"], "%a %b %d %H:%M:%S %z %Y"),
        text=text,
        kind=kind,
        url=f"{BASE_URL}/{handle}/status/{tweet['rest_id']}",
        media=media,
        metrics=metrics,
        raw=tweet,
    )


def parse_timeline(bodies: list[Any], profile: ProfileInfo) -> list[FetchedPost]:
    """Posts authored by `profile` in SearchTimeline responses, newest first."""
    seen: dict[str, FetchedPost] = {}
    for body in bodies:
        for wrapper in _walk(body, "tweet_results"):
            tweet = _unwrap((wrapper or {}).get("result") if isinstance(wrapper, dict) else None)
            if tweet is None or tweet["rest_id"] in seen:
                continue
            if _author_id(tweet) != profile.external_id:
                continue  # quoted/retweeted originals by other people
            seen[tweet["rest_id"]] = to_post(tweet, profile.handle)
    return sorted(seen.values(), key=lambda p: int(p.external_id), reverse=True)


def search_url(handle: str, max_id: str | None) -> str:
    query = f"from:{handle} include:nativeretweets"
    if max_id:
        query += f" max_id:{max_id}"
    return f"{BASE_URL}/search?q={quote(query)}&src=typed_query&f=live"


class XBrowserClient(BrowserClient):
    platform = Platform.x
    label = "X"

    def _check(self, captured: Captured, *, need_data: bool = True) -> None:
        path = captured.final_url.split("x.com", 1)[-1]
        if any(marker in path for marker in _LOGIN_MARKERS):
            raise self.fail("challenged", "X signed this session out or asked for a check")
        if 429 in captured.statuses or captured.status == 429:
            raise self.fail("rate_limited", "X rate-limited this session")
        if need_data and not captured.bodies:
            raise self.fail(
                "error", "X didn't return any data (the site may have changed, or is down)"
            )

    async def resolve(self, handle: str) -> ProfileInfo:
        username = handle.strip().lstrip("@")
        captured = await self._load(
            lambda d: d.collect(
                f"{BASE_URL}/{quote(username)}",
                "/UserByScreenName",
                enough=bool,
                max_scrolls=0,
            )
        )
        self._check(captured, need_data=False)
        profile = parse_user(captured.bodies)
        if profile is None:
            raise ProfileNotFound(f"X account @{username} not found or suspended")
        return profile

    async def fetch_page(self, profile: ProfileInfo, cursor: str | None = None) -> Page:
        def enough(bodies: list[Any]) -> bool:
            return len(parse_timeline(bodies, profile)) >= PAGE_TARGET

        captured = await self._load(
            lambda d: d.collect(
                search_url(profile.handle, cursor),
                "/SearchTimeline",
                enough=enough,
                max_scrolls=MAX_SCROLLS,
            )
        )
        self._check(captured)
        posts = parse_timeline(captured.bodies, profile)
        if not posts:
            return Page(posts=[], next_cursor=None)
        oldest = min(int(p.external_id) for p in posts)
        return Page(posts=posts, next_cursor=str(oldest - 1))
