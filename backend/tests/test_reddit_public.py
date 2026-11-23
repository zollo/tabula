"""Reddit with no login: the public .json client, shared pacing, and wiring."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import platforms
from app.db import AsyncSessionLocal
from app.models.platform import Platform
from app.models.request_pacer import RequestPacer
from app.models.social_account import SocialAccount
from app.platforms import PlatformError, ProfileInfo, ProfileNotFound, Throttled
from app.platforms.reddit_public import RedditPublicClient
from app.services import scan_service
from app.services.pacer_service import Pacer

ABOUT = {"kind": "t2", "data": {"id": "abc", "name": "Bob", "icon_img": "https://i/x.png?s=1"}}
PROFILE = ProfileInfo(external_id="t2_abc", handle="Bob")


def _listing(*names: str, after: str | None = None) -> dict:
    children = [
        {"kind": n[:2], "data": {"name": n, "created_utc": 1_700_000_000 + i, "title": n,
                                  "body": f"comment {n}", "permalink": f"/r/x/comments/{n}/"}}
        for i, n in enumerate(names)
    ]
    return {"kind": "Listing", "data": {"after": after, "children": children}}


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


class Reddit:
    """A scripted reddit.com: answers requests in order, records them."""

    def __init__(self, *responses: httpx.Response):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def client(self, pacer, user_agent="tabula-test/1.0"):
        return RedditPublicClient(pacer, user_agent, transport=httpx.MockTransport(self))


def test_resolve_and_page_without_credentials():
    reddit = Reddit(_json(ABOUT), _json(_listing("t3_a", "t1_b", after="t1_b")))
    pacer = FakePacer()
    client = reddit.client(pacer)

    async def go():
        return await client.resolve("u/bob"), await client.fetch_page(PROFILE, "t3_y")

    profile, page = asyncio.run(go())
    assert profile.external_id == "t2_abc" and profile.avatar_url == "https://i/x.png"
    assert [p.external_id for p in page.posts] == ["t3_a", "t1_b"]
    assert [p.kind for p in page.posts] == ["post", "comment"]
    assert page.next_cursor == "t1_b"
    about, overview = reddit.requests
    assert about.url.path == "/user/bob/about.json"
    assert overview.url.path == "/user/Bob/overview.json"
    assert overview.url.params["after"] == "t3_y" and overview.url.params["limit"] == "100"
    # No login of any kind, and a descriptive user agent.
    assert "authorization" not in overview.headers and "cookie" not in overview.headers
    assert overview.headers["user-agent"] == "tabula-test/1.0"
    assert pacer.waits == 2


def test_missing_users_and_odd_answers():
    reddit = Reddit(
        _json({"message": "Not Found", "error": 404}, 404),
        _json({"kind": "t2", "data": {"is_suspended": True, "name": "gone"}}),
        _json({"reason": "suspended"}, 403),
        # A 200 that isn't a listing must not look like the end of history.
        _json({"error": "x"}),
        httpx.Response(200, text="<html>", headers={"content-type": "text/html"}),
        httpx.Response(503, text="down"),
    )
    client = reddit.client(FakePacer())

    async def go():
        for handle in ("ghost", "gone"):
            with pytest.raises(ProfileNotFound):
                await client.resolve(handle)
        with pytest.raises(ProfileNotFound):
            await client.fetch_page(PROFILE)
        for _ in range(3):
            with pytest.raises(PlatformError) as exc:
                await client.fetch_page(PROFILE)
            assert exc.value.retryable

    asyncio.run(go())


def test_reddit_rate_limits_hold_every_worker():
    reddit = Reddit(
        _json(_listing("t3_a"), **{"x-ratelimit-remaining": "0.0", "x-ratelimit-reset": "42"}),
        httpx.Response(429, text="Too Many Requests", headers={"retry-after": "90"}),
        httpx.Response(403, text="<html>blocked</html>", headers={"content-type": "text/html"}),
    )
    pacer = FakePacer()
    client = reddit.client(pacer)

    async def go():
        page = await client.fetch_page(PROFILE)  # still fine, but the window is spent
        assert len(page.posts) == 1
        for match in ("rate-limited", "blocked"):
            with pytest.raises(PlatformError, match=match) as exc:
                await client.fetch_page(PROFILE)
            assert exc.value.retryable

    asyncio.run(go())
    assert pacer.holds == [42.0, 90.0, 15 * 60]


# ── The shared pacer ─────────────────────────────────────────────────────


def _db(fn):
    async def go():
        async with AsyncSessionLocal() as db:
            return await fn(db)

    return asyncio.run(go())


def test_pacer_spaces_requests_across_workers(client):
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    async def go(db):
        async with AsyncSessionLocal() as other_db:
            a = Pacer(db, "reddit-public", 10, sleep=sleep, max_wait=timedelta(seconds=25))
            b = Pacer(other_db, "reddit-public", 10, sleep=sleep, max_wait=timedelta(seconds=25))
            await a.wait()  # first slot: now
            await b.wait()  # another worker: 10 s later
            await a.wait()  # 20 s later
            with pytest.raises(Throttled) as exc:
                await b.wait()  # 30 s away: over max_wait, don't hold the worker
            row = await db.get(RequestPacer, "reddit-public", populate_existing=True)
            return exc.value.retry_at, row.next_at

    retry_at, next_at = _db(go)
    assert len(slept) == 2
    assert 9 < slept[0] <= 10 and 19 < slept[1] <= 20
    next_at = next_at if next_at.tzinfo else next_at.replace(tzinfo=UTC)
    assert retry_at == next_at  # the refused slot wasn't claimed


def test_pacer_hold_pushes_the_next_slot(client):
    async def go(db):
        pacer = Pacer(db, "reddit-public", 10)
        until = await pacer.hold(300)
        with pytest.raises(Throttled) as exc:
            await pacer.wait()
        await pacer.hold(5)  # a shorter hold never shortens a longer one
        row = await db.get(RequestPacer, "reddit-public", populate_existing=True)
        return until, exc.value.retry_at, row.next_at

    until, retry_at, next_at = _db(go)
    assert retry_at == until
    assert (next_at if next_at.tzinfo else next_at.replace(tzinfo=UTC)) == until


# ── Settings, adding accounts, scanning ──────────────────────────────────


@pytest.fixture
def public_reddit(monkeypatch):
    """Serves the public source from a scripted Reddit, without real sleeps."""
    reddit = Reddit()
    built: list[Pacer] = []

    async def no_sleep(_):
        pass

    def get_public_client(platform, pacer, user_agent):
        assert platform == Platform.reddit
        pacer._sleep = no_sleep
        built.append(pacer)
        return reddit.client(pacer, user_agent)

    monkeypatch.setattr(platforms, "get_public_client", get_public_client)
    reddit.pacers = built
    return reddit


def test_public_source_settings(client, admin, enabled_user):
    _, headers = enabled_user
    body = client.get("/api/settings", headers=admin).json()
    assert body["public_platforms"] == ["reddit"]
    assert body["public_request_interval_seconds"] == 10
    resp = client.patch("/api/settings", headers=admin, json={
        "platform_sources": {"reddit": "public"}, "public_request_interval_seconds": 12})
    assert resp.status_code == 200, resp.text
    assert resp.json()["platform_sources"]["reddit"] == "public"
    assert resp.json()["public_request_interval_seconds"] == 12
    for bad in ({"platform_sources": {"x": "public"}},
                {"public_request_interval_seconds": 2}):
        assert client.patch("/api/settings", headers=admin, json=bad).status_code == 422

    options = client.get("/api/options", headers=headers).json()["platforms"]
    # No Reddit credentials are set, yet Reddit is ready.
    assert next(p for p in options if p["value"] == "reddit")["configured"] is True


def test_adding_and_backfilling_with_no_login(
    client, admin, enabled_user, queued, public_reddit
):
    _, headers = enabled_user
    client.patch("/api/settings", headers=admin, json={"platform_sources": {"reddit": "public"}})
    public_reddit.responses += [_json(ABOUT)]
    resp = client.post("/api/trackings", json={"platform": "reddit", "handle": "u/Bob"},
                       headers=headers)
    assert resp.status_code == 201, resp.text
    account_id = resp.json()["social_account"]["id"]
    assert queued == [("backfill", account_id)]  # API workers, not the browser queue
    assert public_reddit.pacers[0].interval == timedelta(seconds=10)

    public_reddit.responses += [
        _json(_listing("t3_c", "t1_b", after="t1_b")),
        _json(_listing("t3_a")),
    ]

    async def go(db):
        account = await db.get(SocialAccount, uuid.UUID(account_id))
        more = await scan_service.backfill(db, account, pages=5)
        return more, account.post_count, account.backfill_status

    assert _db(go) == (False, 3, "complete")
    assert public_reddit.requests[-1].url.params["after"] == "t1_b"


def test_a_busy_pacer_defers_work_instead_of_failing(client, admin, monkeypatch):
    client.patch("/api/settings", headers=admin, json={"platform_sources": {"reddit": "public"}})
    later = datetime.now(UTC) + timedelta(minutes=3)

    async def go(db):
        db.add(RequestPacer(key="reddit-public", next_at=later))
        account = SocialAccount(platform=Platform.reddit, external_id="t2_a", handle="bob",
                                scan_interval_minutes=60, backfill_status="running")
        db.add(account)
        await db.commit()
        await scan_service.scan(db, account)
        scan_state = (account.next_scan_at, account.last_scanned_at, account.last_error)
        more = await scan_service.backfill(db, account, pages=1)
        return scan_state, more, account.backfill_status, account.backfill_touched_at

    (next_scan, scanned, error), more, status, touched = _db(go)

    def aware(dt):
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    # The scan comes back when the slot frees up, without counting as a failure.
    assert aware(next_scan) == later and scanned is None and error is None
    # The backfill goes back to the scheduler, re-queued once the slot is near.
    assert more is False and status == "pending"
    assert aware(touched) == later - scan_service.REQUEUE_PENDING


def test_switching_to_public_restarts_backfills(client, admin):
    async def go(db):
        account = SocialAccount(platform=Platform.reddit, external_id="t2_a", handle="bob",
                                backfill_status="running", backfill_cursor="t3_x",
                                backfill_touched_at=datetime.now(UTC))
        db.add(account)
        await db.commit()
        return account.id

    account_id = _db(go)
    client.patch("/api/settings", headers=admin, json={"platform_sources": {"reddit": "public"}})

    async def check(db):
        row = await db.get(SocialAccount, account_id)
        return row.backfill_status, row.backfill_cursor

    assert _db(check) == ("pending", None)
