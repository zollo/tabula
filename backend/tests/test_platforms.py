"""Platform clients against canned API responses (httpx.MockTransport)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.models.post import PostKind
from app.platforms import PlatformError, PlatformNotConfigured, ProfileNotFound, get_client
from app.platforms.bluesky import BlueskyClient
from app.platforms.reddit import RedditClient
from app.platforms.x import XClient


def run(coro):
    return asyncio.run(coro)


def test_bluesky_profile_and_feed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("getProfile"):
            if request.url.params["actor"] == "nobody.bsky.social":
                return httpx.Response(400, json={"error": "InvalidRequest"})
            return httpx.Response(200, json={
                "did": "did:plc:abc", "handle": "alice.bsky.social",
                "displayName": "Alice", "avatar": "https://cdn/a.jpg"})
        assert request.url.params["actor"] == "did:plc:abc"
        assert request.url.params.get("cursor") in (None, "c1")
        if request.url.params.get("cursor") == "c1":
            return httpx.Response(200, json={"feed": []})
        return httpx.Response(200, json={"cursor": "c1", "feed": [
            {"post": {"uri": "at://did:plc:abc/app.bsky.feed.post/3k1", "cid": "x",
                      "author": {"handle": "alice.bsky.social"},
                      "record": {"text": "hello", "createdAt": "2026-03-01T10:00:00.000Z"},
                      "embed": {"$type": "app.bsky.embed.images#view",
                                "images": [{"fullsize": "https://cdn/i.jpg"}]},
                      "likeCount": 3, "repostCount": 1, "replyCount": 0,
                      "indexedAt": "2026-03-01T10:00:01Z"}},
            {"post": {"uri": "at://did:plc:zzz/app.bsky.feed.post/9z", "cid": "y",
                      "author": {"handle": "bob.bsky.social"},
                      "record": {"text": "bob's post", "createdAt": "2026-02-01T10:00:00Z"},
                      "indexedAt": "2026-02-01T10:00:00Z"},
             "reason": {"$type": "app.bsky.feed.defs#reasonRepost",
                        "indexedAt": "2026-03-02T09:00:00Z"}},
        ]})

    client = BlueskyClient("https://appview.test", transport=httpx.MockTransport(handler))
    profile = run(client.resolve("@alice.bsky.social"))
    assert profile.external_id == "did:plc:abc"
    assert profile.profile_url == "https://bsky.app/profile/alice.bsky.social"
    page = run(client.fetch_page(profile))
    assert page.next_cursor == "c1"
    post, repost = page.posts
    assert post.kind == PostKind.post and post.media == ["https://cdn/i.jpg"]
    assert post.url == "https://bsky.app/profile/alice.bsky.social/post/3k1"
    assert post.metrics["likes"] == 3
    assert repost.kind == PostKind.repost
    assert repost.external_id.startswith("repost:")
    assert repost.posted_at.isoformat() == "2026-03-02T09:00:00+00:00"
    assert run(client.fetch_page(profile, "c1")).next_cursor is None
    with pytest.raises(ProfileNotFound):
        run(client.resolve("nobody.bsky.social"))


def test_reddit_profile_and_overview():
    tokens = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/access_token":
            tokens.append(request.headers["authorization"])
            return httpx.Response(200, json={"access_token": "tok"})
        assert request.headers["authorization"] == "Bearer tok"
        assert request.headers["user-agent"] == "ua/1"
        if request.url.path == "/user/spez/about":
            return httpx.Response(200, json={"data": {
                "id": "1w72", "name": "spez", "icon_img": "https://i/x.png?s=1",
                "subreddit": {"title": "Steve"}}})
        if request.url.path == "/user/missing/about":
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"data": {"after": "t1_b", "children": [
            {"kind": "t3", "data": {"name": "t3_a", "title": "A title", "selftext": "body",
                                    "created_utc": 1767225600, "permalink": "/r/x/comments/a/",
                                    "score": 10, "num_comments": 2,
                                    "subreddit_name_prefixed": "r/x"}},
            {"kind": "t1", "data": {"name": "t1_b", "body": "a comment",
                                    "created_utc": 1767225000, "permalink": "/r/x/comments/a/b/",
                                    "score": 1}},
        ]}})

    client = RedditClient("id", "secret", "ua/1", transport=httpx.MockTransport(handler))
    profile = run(client.resolve("u/spez"))
    assert profile.external_id == "t2_1w72"
    assert profile.avatar_url == "https://i/x.png"
    page = run(client.fetch_page(profile))
    assert page.next_cursor == "t1_b"
    post, comment = page.posts
    assert post.text == "A title\n\nbody" and post.kind == PostKind.post
    assert post.url == "https://www.reddit.com/r/x/comments/a/"
    assert comment.kind == PostKind.comment
    assert len(tokens) == 1  # token reused
    with pytest.raises(ProfileNotFound):
        run(client.resolve("missing"))


def test_x_profile_and_timeline():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer xt"
        if request.url.path == "/2/users/by/username/jack":
            return httpx.Response(200, json={"data": {
                "id": "12", "username": "jack", "name": "Jack",
                "profile_image_url": "https://pbs/x.jpg"}})
        if request.url.path == "/2/users/by/username/nobody":
            return httpx.Response(200, json={"errors": [{"title": "Not Found Error"}]})
        if request.url.path == "/2/users/by/username/limited":
            return httpx.Response(429, json={})
        return httpx.Response(200, json={
            "data": [
                {"id": "20", "text": "just setting up", "created_at": "2006-03-21T20:50:14.000Z",
                 "public_metrics": {"like_count": 5, "retweet_count": 2},
                 "attachments": {"media_keys": ["m1"]}},
                {"id": "21", "text": "RT @x: hi", "created_at": "2006-03-22T20:50:14.000Z",
                 "referenced_tweets": [{"type": "retweeted", "id": "1"}]},
            ],
            "includes": {"media": [{"media_key": "m1", "url": "https://pbs/m1.jpg"}]},
            "meta": {"next_token": "nt"},
        })

    client = XClient("xt", transport=httpx.MockTransport(handler))
    profile = run(client.resolve("@jack"))
    assert profile.external_id == "12"
    page = run(client.fetch_page(profile))
    assert page.next_cursor == "nt"
    first, rt = page.posts
    assert first.url == "https://x.com/jack/status/20"
    assert first.media == ["https://pbs/m1.jpg"]
    assert first.metrics["likes"] == 5
    assert rt.kind == PostKind.repost
    with pytest.raises(ProfileNotFound):
        run(client.resolve("nobody"))
    with pytest.raises(PlatformError) as err:
        run(client.resolve("limited"))
    assert err.value.retryable


def test_missing_credentials():
    creds = {"x_bearer_token": "", "reddit_client_id": "", "reddit_client_secret": "",
             "reddit_user_agent": "ua", "bluesky_appview_url": "https://appview.test"}
    from app.models.platform import Platform

    with pytest.raises(PlatformNotConfigured):
        get_client(Platform.x, creds)
    with pytest.raises(PlatformNotConfigured):
        get_client(Platform.reddit, creds)
    assert isinstance(get_client(Platform.bluesky, creds), BlueskyClient)
