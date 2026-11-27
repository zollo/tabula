"""Reddit from the Arctic Shift archive: the client, paging, and wiring."""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from app import platforms
from app.db import AsyncSessionLocal
from app.models.platform import Platform
from app.models.social_account import SocialAccount
from app.platforms import PlatformError, ProfileInfo, ProfileNotFound
from app.platforms.reddit_archive import PAGE_SIZE, RedditArchiveClient
from app.services import scan_service
from app.services.pacer_service import Pacer

PROFILE = ProfileInfo(external_id="t2_abc", handle="Bob")
T0 = 1_790_000_000


def _post(i: int, t: int) -> dict:
    return {"id": f"p{i}", "name": f"t3_p{i}", "created_utc": t, "title": f"post {i}",
            "author": "Bob", "author_fullname": "t2_abc", "score": 3, "num_comments": 1,
            "subreddit_name_prefixed": "r/x", "permalink": f"/r/x/comments/p{i}/"}


def _comment(i: int, t: int) -> dict:
    # Some archived records lack `name`; the client derives it from `id`.
    return {"id": f"c{i}", "created_utc": t, "body": f"comment {i}", "author": "Bob",
            "author_fullname": "t2_abc", "score": 1, "subreddit_name_prefixed": "r/x",
            "permalink": f"/r/x/comments/p0/x/c{i}/"}


def _json(body, status=200, **headers) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body),
                          headers={"content-type": "application/json", **headers})


class FakePacer:
    def __init__(self):
        self.waits = 0
        self.holds: list[float] = []

    async def wait(self):
        self.waits += 1

    async def hold(self, seconds):
        self.holds.append(seconds)


class Archive:
    """A scripted Arctic Shift: answers requests in order, records them."""

    def __init__(self, *responses: httpx.Response):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def client(self, pacer, user_agent="tabula-test/1.0"):
        return RedditArchiveClient(pacer, user_agent, transport=httpx.MockTransport(self))


def _db(fn):
    async def go():
        async with AsyncSessionLocal() as db:
            return await fn(db)

    return asyncio.run(go())


def test_resolve_from_latest_post_or_comment():
    archive = Archive(
        _json({"data": [{**_post(1, T0), "author": "bob_Real"}]}),
        _json({"data": []}),  # no posts: falls back to comments
        _json({"data": [_comment(1, T0)]}),
        _json({"data": []}),
        _json({"data": []}),
    )
    pacer = FakePacer()
    client = archive.client(pacer)

    async def go():
        first = await client.resolve("u/bob_real")
        second = await client.resolve("Bob")
        with pytest.raises(ProfileNotFound):
            await client.resolve("ghost")
        return first, second

    first, second = asyncio.run(go())
    # The archive's spelling of the name wins; the id matches the API's t2_….
    assert (first.external_id, first.handle) == ("t2_abc", "bob_Real")
    assert first.profile_url == "https://www.reddit.com/user/bob_Real"
    assert second.external_id == "t2_abc"
    req = archive.requests[0]
    assert req.url.path == "/api/posts/search"
    assert req.url.params["author"] == "bob_real" and req.url.params["limit"] == "1"
    assert archive.requests[2].url.path == "/api/comments/search"
    assert "authorization" not in req.headers and req.headers["user-agent"] == "tabula-test/1.0"
    assert pacer.waits == 5


def test_a_page_merges_posts_and_comments_newest_first():
    archive = Archive(
        _json({"data": [_post(2, T0 + 30), _post(1, T0 + 10)]}),
        _json({"data": [_comment(1, T0 + 20)]}),
    )
    page = asyncio.run(archive.client(FakePacer()).fetch_page(PROFILE))
    assert [p.external_id for p in page.posts] == ["t3_p2", "t1_c1", "t3_p1"]
    assert [p.kind for p in page.posts] == ["post", "comment", "post"]
    assert page.posts[1].url == "https://www.reddit.com/r/x/comments/p0/x/c1/"
    assert page.next_cursor is None  # neither list was full: that's all of history
    for req in archive.requests:
        assert req.url.params["sort"] == "desc" and "before" not in req.url.params


def test_full_lists_page_back_without_gaps_or_overlap():
    # 100 posts reaching back to T0+100; comments only go back to T0+150, all
    # newer than that. Older comments may exist, so cut at the newer cut-off.
    posts = [_post(i, T0 + 199 - i) for i in range(PAGE_SIZE)]  # T0+199 … T0+100
    comments = [_comment(i, T0 + 250 - i) for i in range(PAGE_SIZE)]  # T0+250 … T0+151
    archive = Archive(
        _json({"data": posts}), _json({"data": comments}),
        _json({"data": []}), _json({"data": [_comment(900, T0 + 151), _comment(901, T0 + 5)]}),
    )
    client = archive.client(FakePacer())

    async def go():
        first = await client.fetch_page(PROFILE)
        second = await client.fetch_page(PROFILE, first.next_cursor)
        return first, second

    first, second = asyncio.run(go())
    # Items from the cut-off second (T0+151) itself wait for the next page.
    assert first.next_cursor == str(T0 + 151)
    times = [int(p.posted_at.timestamp()) for p in first.posts]
    assert min(times) == T0 + 152 and times == sorted(times, reverse=True)
    assert archive.requests[2].url.params["before"] == str(T0 + 152)  # inclusive of T0+151
    assert [p.external_id for p in second.posts] == ["t1_c900", "t1_c901"]
    assert second.next_cursor is None


def test_a_page_all_in_one_second_still_moves_on():
    posts = [_post(i, T0) for i in range(PAGE_SIZE)]
    archive = Archive(_json({"data": posts}), _json({"data": []}))
    page = asyncio.run(archive.client(FakePacer()).fetch_page(PROFILE, str(T0)))
    assert len(page.posts) == PAGE_SIZE and page.next_cursor == str(T0 - 1)


def test_errors_are_retried_and_429s_hold_everyone():
    archive = Archive(
        _json({"data": None, "error": "Timeout. Maybe slow down a bit."}, 422),
        httpx.Response(200, text="<html>", headers={"content-type": "text/html"}),
        _json({"data": "nope"}),
        httpx.Response(503, text="down"),
        _json({"error": "slow down"}, 429, **{"x-ratelimit-reset": "37"}),
    )
    pacer = FakePacer()
    client = archive.client(pacer)

    async def go():
        for _ in range(5):
            with pytest.raises(PlatformError) as exc:
                await client.fetch_page(PROFILE)
            assert exc.value.retryable

    asyncio.run(go())
    assert pacer.holds == [37.0]


@pytest.fixture
def archive_reddit(monkeypatch):
    """Serves the archive source from a scripted Arctic Shift, without real sleeps."""
    archive = Archive()
    built: list[Pacer] = []

    async def no_sleep(_):
        pass

    def get_archive_client(platform, pacer, user_agent):
        assert platform == Platform.reddit
        pacer._sleep = no_sleep
        built.append(pacer)
        return archive.client(pacer, user_agent)

    monkeypatch.setattr(platforms, "get_archive_client", get_archive_client)
    archive.pacers = built
    return archive


def test_archive_source_settings(client, admin, enabled_user):
    _, headers = enabled_user
    body = client.get("/api/settings", headers=admin).json()
    assert body["archive_platforms"] == ["reddit"]
    resp = client.patch("/api/settings", headers=admin,
                        json={"platform_sources": {"reddit": "archive"}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["platform_sources"]["reddit"] == "archive"
    for platform in ("x", "bluesky"):
        bad = {"platform_sources": {platform: "archive"}}
        assert client.patch("/api/settings", headers=admin, json=bad).status_code == 422

    options = client.get("/api/options", headers=headers).json()["platforms"]
    # No Reddit credentials are set, yet Reddit is ready.
    assert next(p for p in options if p["value"] == "reddit")["configured"] is True


def test_adding_and_backfilling_from_the_archive(
    client, admin, enabled_user, queued, archive_reddit
):
    _, headers = enabled_user
    client.patch("/api/settings", headers=admin, json={"platform_sources": {"reddit": "archive"}})
    archive_reddit.responses += [_json({"data": [_post(1, T0)]})]
    resp = client.post("/api/trackings", json={"platform": "reddit", "handle": "u/bob"},
                       headers=headers)
    assert resp.status_code == 201, resp.text
    account = resp.json()["social_account"]
    assert account["display_name"] == "Bob"
    assert queued == [("backfill", account["id"])]  # API workers, not the browser queue
    pacer = archive_reddit.pacers[0]
    assert pacer.key == "reddit-archive" and pacer.interval.total_seconds() == 2

    archive_reddit.responses += [
        _json({"data": [_post(3, T0 + 30), _post(1, T0)]}),
        _json({"data": [_comment(1, T0 + 20)]}),
    ]

    async def go(db):
        row = await db.get(SocialAccount, uuid.UUID(account["id"]))
        more = await scan_service.backfill(db, row, pages=5)
        return more, row.post_count, row.backfill_status

    assert _db(go) == (False, 3, "complete")
    assert archive_reddit.requests[-1].url.params["author"] == "bob"
