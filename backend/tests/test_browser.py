"""Browser collection: parsing, the browser clients, saved sessions and routing.

No real browser: `FakeDriver` replays captured JSON the way PlaywrightDriver
would hand it over.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from app import platforms
from app.db import AsyncSessionLocal
from app.models.platform import Platform
from app.models.post import PostKind
from app.models.scraper_session import ScraperSession, SessionStatus
from app.models.social_account import SocialAccount
from app.platforms import PlatformError, ProfileInfo, ProfileNotFound
from app.platforms.browser import Captured, RedditBrowserClient, XBrowserClient
from app.platforms.browser import x as xb
from app.services import scan_service, settings_service
from app.services import scraper_session_service as sessions
from app.workers import dispatch

X_STATE = json.dumps({"cookies": [{"name": "auth_token", "value": "t", "domain": ".x.com"}],
                      "origins": []})
PROFILE = ProfileInfo(external_id="42", handle="alice", display_name="Alice")


def _tweet(tid: int, text: str = "hi", *, author: str = "42", **legacy) -> dict:
    return {
        "__typename": "Tweet",
        "rest_id": str(tid),
        "core": {"user_results": {"result": {"rest_id": author}}},
        "views": {"count": "10"},
        "legacy": {
            "full_text": text,
            "created_at": "Wed Oct 10 20:19:24 +0000 2018",
            "user_id_str": author,
            "favorite_count": 3,
            "retweet_count": 1,
            "reply_count": 0,
            "quote_count": 0,
            **legacy,
        },
    }


def _timeline(*tweets: dict) -> dict:
    entries = [
        {"content": {"itemContent": {"tweet_results": {"result": t}}}} for t in tweets
    ]
    return {"data": {"search_by_raw_query": {"search_timeline": {"timeline": {
        "instructions": [{"type": "TimelineAddEntries", "entries": entries}]}}}}}


class FakeDriver:
    def __init__(self, responses: list[Captured]):
        self.responses = list(responses)
        self.urls: list[str] = []
        self.state = "unset"
        self.closed = False

    async def start(self, storage_state):
        self.state = storage_state

    async def collect(self, url, match, *, enough, max_scrolls):
        self.urls.append(url)
        return self.responses.pop(0)

    async def get_json(self, url):
        self.urls.append(url)
        return self.responses.pop(0)

    async def close(self):
        self.closed = True


class FakeBroker:
    interval_seconds = 20

    def __init__(self, state=None):
        self.state = state
        self.pages = 0
        self.released: tuple[str, str | None] | None = None

    async def acquire(self):
        return self.state

    async def page_loaded(self):
        self.pages += 1

    async def release(self, outcome, error):
        self.released = (outcome, error)


def _client(cls, responses, broker=None):
    driver = FakeDriver(responses)
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    client = cls(broker or FakeBroker({"cookies": []}), lambda: driver, sleep=sleep,
                 clock=lambda: 0.0)
    return client, driver, slept


# ── X parsing ────────────────────────────────────────────────────────────


def test_x_timeline_parsing_handles_every_kind():
    long = _tweet(105, "short")
    long["note_tweet"] = {"note_tweet_results": {"result": {"text": "the full long text"}}}
    retweet = _tweet(104, "RT @bob: orig", retweeted_status_result={
        "result": _tweet(7, "bob's post", author="99",
                         extended_entities={"media": [{"media_url_https": "https://img/1"}]})})
    reply = _tweet(103, "@bob yes", in_reply_to_status_id_str="1")
    quote = {"__typename": "TweetWithVisibilityResults",
             "tweet": _tweet(102, "look", is_quote_status=True)}
    other = _tweet(101, "not alice", author="99")
    posts = xb.parse_timeline([_timeline(reply, long, other), _timeline(retweet, quote)],
                              PROFILE)
    assert [p.external_id for p in posts] == ["105", "104", "103", "102"]
    by_id = {p.external_id: p for p in posts}
    assert by_id["105"].text == "the full long text"
    assert by_id["104"].kind == PostKind.repost
    assert by_id["104"].text == "bob's post"
    assert by_id["104"].media == ["https://img/1"]
    assert by_id["103"].kind == PostKind.reply
    assert by_id["102"].kind == PostKind.quote
    assert by_id["105"].metrics == {"likes": 3, "reposts": 1, "replies": 0, "quotes": 0,
                                    "views": 10}
    assert by_id["105"].posted_at == datetime(2018, 10, 10, 20, 19, 24, tzinfo=UTC)
    assert by_id["105"].url == "https://x.com/alice/status/105"


@pytest.mark.parametrize("shape", ["current", "legacy"])
def test_x_user_parsing(shape):
    result = {"__typename": "User", "rest_id": "42", "legacy": {}}
    if shape == "current":
        result["core"] = {"screen_name": "Alice", "name": "Alice A"}
        result["avatar"] = {"image_url": "https://pbs/a.jpg"}
    else:
        result["legacy"] = {"screen_name": "Alice", "name": "Alice A",
                            "profile_image_url_https": "https://pbs/a.jpg"}
    profile = xb.parse_user([{"data": {"user": {"result": result}}}])
    assert profile == ProfileInfo(external_id="42", handle="Alice", display_name="Alice A",
                                  avatar_url="https://pbs/a.jpg",
                                  profile_url="https://x.com/Alice")
    assert xb.parse_user([{"data": {"user": {"result": {"__typename": "UserUnavailable"}}}}]) \
        is None


# ── X client ─────────────────────────────────────────────────────────────


def test_x_client_pages_back_with_max_id_and_paces():
    page1 = Captured("https://x.com/search", 200, [_timeline(_tweet(300), _tweet(250))], [200])
    page2 = Captured("https://x.com/search", 200, [_timeline()], [200])
    broker = FakeBroker({"cookies": ["c"]})
    client, driver, slept = _client(XBrowserClient, [page1, page2], broker)

    async def go():
        first = await client.fetch_page(PROFILE)
        second = await client.fetch_page(PROFILE, first.next_cursor)
        await client.aclose()
        return first, second

    first, second = asyncio.run(go())
    assert [p.external_id for p in first.posts] == ["300", "250"]
    assert first.next_cursor == "249"
    assert second.posts == [] and second.next_cursor is None  # end of history
    assert "max_id%3A249" in driver.urls[1] and "max_id" not in driver.urls[0]
    assert "from%3Aalice" in driver.urls[0] and "f=live" in driver.urls[0]
    assert driver.state == {"cookies": ["c"]}
    assert slept == [20]  # waited between the two page loads
    assert broker.pages == 2
    assert broker.released == ("ok", None)
    assert driver.closed


@pytest.mark.parametrize(
    ("captured", "outcome"),
    [
        (Captured("https://x.com/i/flow/login", 200, [], []), "challenged"),
        (Captured("https://x.com/search", 200, [], [429]), "rate_limited"),
        (Captured("https://x.com/search", 200, [], []), "error"),
    ],
)
def test_x_client_reports_blocks(captured, outcome):
    broker = FakeBroker()
    client, _, _ = _client(XBrowserClient, [captured], broker)

    async def go():
        try:
            with pytest.raises(PlatformError) as err:
                await client.fetch_page(PROFILE)
            assert err.value.retryable
        finally:
            await client.aclose()

    asyncio.run(go())
    assert broker.released[0] == outcome


def test_x_client_resolve():
    user = {"data": {"user": {"result": {"__typename": "User", "rest_id": "42",
                                         "legacy": {"screen_name": "Alice", "name": "A"}}}}}
    client, driver, _ = _client(XBrowserClient, [
        Captured("https://x.com/Alice", 200, [user], [200]),
        Captured("https://x.com/ghost", 200, [], []),
    ])

    async def go():
        assert (await client.resolve("@Alice")).external_id == "42"
        with pytest.raises(ProfileNotFound):
            await client.resolve("ghost")

    asyncio.run(go())
    assert driver.urls[0] == "https://x.com/Alice"


# ── Reddit client ────────────────────────────────────────────────────────


def test_reddit_client():
    about = {"data": {"id": "abc", "name": "Bob", "icon_img": "https://i/x.png?s=1"}}
    listing = {"data": {"after": "t3_z", "children": [{"kind": "t3", "data": {
        "name": "t3_a", "created_utc": 1_700_000_000, "title": "Hello",
        "permalink": "/r/x/comments/a/"}}]}}
    client, driver, _ = _client(RedditBrowserClient, [
        Captured("https://www.reddit.com/user/bob/about.json", 200, [about]),
        Captured("https://www.reddit.com/user/Bob/overview.json", 200, [listing]),
        Captured("https://www.reddit.com/user/ghost/about.json", 404, [{"error": 404}]),
        Captured("https://www.reddit.com/user/Bob/overview.json", 403, []),
    ])

    async def go():
        profile = await client.resolve("u/bob")
        page = await client.fetch_page(profile, "t3_y")
        with pytest.raises(ProfileNotFound):
            await client.resolve("ghost")
        with pytest.raises(PlatformError):  # an HTML "blocked" page
            await client.fetch_page(profile)
        await client.aclose()
        return profile, page

    profile, page = asyncio.run(go())
    assert profile.external_id == "t2_abc" and profile.avatar_url == "https://i/x.png"
    assert [p.external_id for p in page.posts] == ["t3_a"] and page.next_cursor == "t3_z"
    assert driver.urls[1].endswith("&after=t3_y")
    assert client.broker.released[0] == "rate_limited"


# ── Saved sessions: validation, leasing, pacing ──────────────────────────


def _db(fn):
    async def go():
        async with AsyncSessionLocal() as db:
            return await fn(db)

    return asyncio.run(go())


def test_session_files_are_validated(client, admin):
    def create(platform, state):
        return client.post("/api/browser-sessions", headers=admin,
                           json={"platform": platform, "label": "main", "storage_state": state})

    assert create("x", None).status_code == 422  # X needs a login
    assert create("x", "not json").status_code == 422
    signed_out = json.dumps({"cookies": [{"name": "guest_id"}]})
    assert "isn't signed in" in create("x", signed_out).json()["detail"]
    assert create("bluesky", X_STATE).status_code == 422
    assert create("reddit", None).status_code == 201  # signed-out Reddit is fine
    resp = create("x", X_STATE)
    assert resp.status_code == 201
    body = resp.json()
    assert body["signed_in"] is True and body["status"] == "active"
    assert "storage_state" not in body and "auth_token" not in resp.text

    listed = client.get("/api/browser-sessions", headers=admin).json()
    assert {s["platform"] for s in listed} == {"x", "reddit"}


def test_sessions_are_admin_only(client, enabled_user):
    _, headers = enabled_user
    assert client.get("/api/browser-sessions", headers=headers).status_code == 403
    assert client.post("/api/browser-sessions", headers=headers,
                       json={"platform": "reddit", "label": "x"}).status_code == 403


def test_leasing_is_exclusive_paced_and_capped(client):
    async def go(db):
        a = await sessions.create_session(db, Platform.x, "a", X_STATE)
        b = await sessions.create_session(db, Platform.x, "b", X_STATE)
        first = await sessions.lease(db, Platform.x, daily_cap=0)
        second = await sessions.lease(db, Platform.x, daily_cap=0)
        assert {first.session_id, second.session_id} == {a.id, b.id}
        assert first.storage_state["cookies"][0]["name"] == "auth_token"
        with pytest.raises(sessions.NoSessionAvailable):  # both busy
            await sessions.lease(db, Platform.x, daily_cap=0)

        # A released session waits out its pacing interval.
        await sessions.record_page(db, first)
        await sessions.release(db, first, interval_seconds=60)
        with pytest.raises(sessions.NoSessionAvailable, match="next free at"):
            await sessions.lease(db, Platform.x, daily_cap=0)
        row = await db.get(ScraperSession, first.session_id, populate_existing=True)
        assert row.pages_today == 1 and row.lease_token is None
        row.next_allowed_at = None
        await db.commit()
        third = await sessions.lease(db, Platform.x, daily_cap=0)
        assert third.session_id == first.session_id
        # A stale lease token can't release someone else's lease.
        await sessions.release(db, first, outcome="challenged")
        row = await db.get(ScraperSession, first.session_id, populate_existing=True)
        assert row.status == SessionStatus.active and row.lease_token == third.token

        # Daily cap: `first` loaded a page today, so with a cap of 1 only `second` is left.
        await sessions.release(db, third)
        await sessions.release(db, second)
        for r in (a, b):
            r = await db.get(ScraperSession, r.id, populate_existing=True)
            r.next_allowed_at = None
        await db.commit()
        capped = await sessions.lease(db, Platform.x, daily_cap=1)
        assert capped.session_id == second.session_id
        await sessions.record_page(db, capped)
        await sessions.release(db, capped)
        row = await db.get(ScraperSession, capped.session_id, populate_existing=True)
        row.next_allowed_at = None
        await db.commit()
        with pytest.raises(sessions.NoSessionAvailable, match="daily page cap"):
            await sessions.lease(db, Platform.x, daily_cap=1)

    _db(go)


def test_blocked_sessions_cool_down_or_need_attention(client, admin):
    async def go(db):
        a = await sessions.create_session(db, Platform.x, "a", X_STATE)
        lease = await sessions.lease(db, Platform.x, daily_cap=0)
        await sessions.release(db, lease, outcome="rate_limited")
        row = await db.get(ScraperSession, a.id, populate_existing=True)
        assert row.status == SessionStatus.cooling_down
        with pytest.raises(sessions.NoSessionAvailable):
            await sessions.lease(db, Platform.x, daily_cap=0)
        row.cooldown_until = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
        lease = await sessions.lease(db, Platform.x, daily_cap=0)  # cooled down
        await sessions.release(db, lease, outcome="challenged", error="login wall")
        assert not await sessions.has_usable_session(db, Platform.x)
        with pytest.raises(sessions.NoSessionAvailable, match="add one in Settings"):
            await sessions.lease(db, Platform.x, daily_cap=0)
        return str(a.id)

    session_id = _db(go)
    row = client.get("/api/browser-sessions", headers=admin).json()[0]
    assert row["status"] == "needs_attention" and row["last_error"] == "login wall"
    # Re-enabling (e.g. after replacing the login) clears it.
    resp = client.patch(f"/api/browser-sessions/{session_id}", headers=admin,
                        json={"storage_state": X_STATE})
    assert resp.json()["status"] == "active" and resp.json()["last_error"] is None
    assert client.delete(f"/api/browser-sessions/{session_id}",
                         headers=admin).status_code == 204


# ── Settings, adding accounts, scanning ──────────────────────────────────


def test_source_settings(client, admin, enabled_user):
    _, headers = enabled_user
    body = client.get("/api/settings", headers=admin).json()
    assert body["platform_sources"] == {"x": "api", "bluesky": "api", "reddit": "api"}
    assert body["browser_platforms"] == ["x", "reddit"]
    resp = client.patch("/api/settings", headers=admin, json={
        "platform_sources": {"x": "browser"}, "browser_page_interval_seconds": 30})
    assert resp.status_code == 200
    assert resp.json()["platform_sources"]["x"] == "browser"
    assert resp.json()["browser_page_interval_seconds"] == 30
    bad = client.patch("/api/settings", headers=admin,
                       json={"platform_sources": {"bluesky": "browser"}})
    assert bad.status_code == 422

    def x_ready():
        options = client.get("/api/options", headers=headers).json()["platforms"]
        return next(p for p in options if p["value"] == "x")["configured"]

    assert x_ready() is False  # browser source, but no session yet
    client.post("/api/browser-sessions", headers=admin,
                json={"platform": "x", "label": "main", "storage_state": X_STATE})
    assert x_ready() is True


def test_adding_a_browser_account_goes_through_the_browser_worker(
    client, admin, enabled_user, queued, monkeypatch
):
    _, headers = enabled_user
    client.patch("/api/settings", headers=admin, json={"platform_sources": {"x": "browser"}})
    track = {"platform": "x", "handle": "@Alice"}
    resp = client.post("/api/trackings", json=track, headers=headers)
    assert resp.status_code == 422 and "browser session" in resp.json()["detail"]

    client.post("/api/browser-sessions", headers=admin,
                json={"platform": "x", "label": "main", "storage_state": X_STATE})
    looked_up = []

    async def resolve_in_browser(platform, handle):
        looked_up.append((platform, handle))
        return ProfileInfo(external_id="42", handle="Alice", display_name="Alice")

    monkeypatch.setattr(dispatch, "resolve_in_browser", resolve_in_browser)
    resp = client.post("/api/trackings", json=track, headers=headers)
    assert resp.status_code == 201, resp.text
    assert looked_up == [(Platform.x, "alice")]
    assert queued == [("backfill@browser", resp.json()["social_account"]["id"])]


def test_backfill_with_the_browser_uses_and_releases_a_session(client, admin, monkeypatch):
    client.patch("/api/settings", headers=admin, json={
        "platform_sources": {"x": "browser"}, "browser_page_interval_seconds": 5})
    driver = FakeDriver([
        Captured("https://x.com/search", 200, [_timeline(_tweet(300), _tweet(250))], [200]),
        Captured("https://x.com/search", 200, [_timeline(_tweet(200))], [200]),
        Captured("https://x.com/search", 200, [_timeline()], [200]),
    ])

    async def no_sleep(_):
        pass

    def get_browser_client(platform, broker):
        assert broker.interval_seconds == 5
        return XBrowserClient(broker, lambda: driver, sleep=no_sleep)

    monkeypatch.setattr(platforms, "get_browser_client", get_browser_client)

    async def go(db):
        session = await sessions.create_session(db, Platform.x, "main", X_STATE)
        account = SocialAccount(platform=Platform.x, external_id="42", handle="alice")
        db.add(account)
        await db.commit()
        more = await scan_service.backfill(db, account, pages=5)
        row = await db.get(ScraperSession, session.id, populate_existing=True)
        return more, account.post_count, account.backfill_status, row

    more, count, status, row = _db(go)
    assert more is False and count == 3 and status == "complete"
    assert row.pages_today == 3 and row.lease_token is None and row.last_error is None
    assert driver.closed


def test_backfill_waits_when_no_session_is_free(client, admin):
    client.patch("/api/settings", headers=admin, json={"platform_sources": {"reddit": "browser"}})

    async def go(db):
        account = SocialAccount(platform=Platform.reddit, external_id="t2_a", handle="bob")
        db.add(account)
        await db.commit()
        more = await scan_service.backfill(db, account, pages=1)
        return more, account.backfill_status, account.last_error

    more, status, error = _db(go)
    assert more is False
    assert status == "running"  # retryable: the scheduler re-queues it later
    assert "add one in Settings" in error


def test_queue_routing():
    assert dispatch.queue_for("browser") == "browser"
    assert dispatch.queue_for("api") == "default"

    async def go(db):
        cfg = await settings_service.get(db)
        cfg.platform_sources = {"x": "browser", "bluesky": "browser"}
        assert settings_service.platform_source(cfg, Platform.x) == "browser"
        # Bluesky has no browser client, whatever is stored.
        assert settings_service.platform_source(cfg, Platform.bluesky) == "api"

    _db(go)


def test_resolve_in_browser_maps_worker_answers(monkeypatch):
    answers = iter([
        {"profile": {"external_id": "1", "handle": "a", "display_name": "A",
                     "avatar_url": None, "profile_url": None}},
        {"not_found": True, "error": "gone"},
        {"error": "busy", "retryable": True},
    ])
    monkeypatch.setattr(dispatch, "_resolve_blocking", lambda p, h: next(answers))

    async def go():
        assert (await dispatch.resolve_in_browser(Platform.x, "a")).external_id == "1"
        with pytest.raises(ProfileNotFound):
            await dispatch.resolve_in_browser(Platform.x, "a")
        with pytest.raises(PlatformError) as err:
            await dispatch.resolve_in_browser(Platform.x, "a")
        assert err.value.retryable

    asyncio.run(go())

    def boom(p, h):
        raise TimeoutError

    monkeypatch.setattr(dispatch, "_resolve_blocking", boom)
    with pytest.raises(PlatformError, match="didn't answer"):
        asyncio.run(dispatch.resolve_in_browser(Platform.x, "a"))


