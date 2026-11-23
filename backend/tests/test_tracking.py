"""Tracking accounts end to end: permissions, backfill, scans, browsing and export."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.db import AsyncSessionLocal
from app.models.social_account import SocialAccount
from app.services import scan_service
from tests.conftest import register


def _track(client, headers, handle="alice", platform="bluesky", interval=None):
    body = {"platform": platform, "handle": handle}
    if interval is not None:
        body["scan_interval_minutes"] = interval
    return client.post("/api/trackings", json=body, headers=headers)


def _run(coro_fn, account_id: str):
    async def go():
        async with AsyncSessionLocal() as db:
            account = await db.get(SocialAccount, uuid.UUID(account_id))
            return await coro_fn(db, account)

    return asyncio.run(go())


def backfill_all(account_id: str) -> None:
    while _run(lambda db, a: scan_service.backfill(db, a, settings.backfill_pages_per_run),
               account_id):
        pass


def test_new_users_must_be_enabled_before_tracking(client, admin, user, fake_platform, queued):
    user_id, headers = user
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["is_enabled"] is False and me["can_track"] is False
    resp = _track(client, headers)
    assert resp.status_code == 403
    assert "administrator" in resp.json()["detail"]

    # Users can't enable themselves.
    assert client.patch(f"/api/users/{user_id}", json={"is_enabled": True},
                        headers=headers).status_code == 403
    client.patch(f"/api/users/{user_id}", json={"is_enabled": True}, headers=admin)
    assert _track(client, headers).status_code == 201


def test_add_tracking_backfills_history(client, enabled_user, fake_platform, queued):
    _, headers = enabled_user
    resp = _track(client, headers, handle="@Alice", interval=30)
    assert resp.status_code == 201, resp.text
    tracking = resp.json()
    account = tracking["social_account"]
    assert account["handle"] == "alice"
    assert account["backfill_status"] == "pending"
    assert account["scan_interval_minutes"] == 30
    assert queued == [("backfill", account["id"])]

    backfill_all(account["id"])
    account = client.get(f"/api/accounts/{account['id']}", headers=headers).json()
    assert account["backfill_status"] == "complete"
    assert account["post_count"] == 250

    page = client.get(f"/api/accounts/{account['id']}/posts?limit=10", headers=headers).json()
    assert page["total"] == 250
    assert page["items"][0]["external_id"] == "p249"  # newest first

    # Tracking it twice is refused.
    assert _track(client, headers, handle="alice").status_code == 409


def test_backfill_respects_post_limit(client, admin, enabled_user, fake_platform, queued):
    client.patch("/api/settings", json={"backfill_max_posts": 150}, headers=admin)
    _, headers = enabled_user
    account_id = _track(client, headers).json()["social_account"]["id"]
    backfill_all(account_id)
    account = client.get(f"/api/accounts/{account_id}", headers=headers).json()
    assert account["backfill_status"] == "complete"
    assert account["post_count"] == 200  # stops after the page that crosses the limit


def test_scan_picks_up_only_new_posts(client, enabled_user, fake_platform, queued):
    _, headers = enabled_user
    account_id = _track(client, headers).json()["social_account"]["id"]
    backfill_all(account_id)
    fake_platform.add(3)
    fake_platform.calls = 0
    assert _run(scan_service.scan, account_id) == 3
    assert fake_platform.calls == 1
    account = client.get(f"/api/accounts/{account_id}", headers=headers).json()
    assert account["post_count"] == 253
    assert account["last_scanned_at"] is not None
    assert _run(scan_service.scan, account_id) == 0


def test_browse_filters(client, enabled_user, fake_platform, queued):
    _, headers = enabled_user
    account_id = _track(client, headers).json()["social_account"]["id"]
    backfill_all(account_id)
    url = f"/api/accounts/{account_id}/posts"
    assert client.get(f"{url}?q=CATS", headers=headers).json()["total"] == 25
    assert client.get(f"{url}?kind=reply", headers=headers).json()["total"] == 36
    since = (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=240)).isoformat()
    resp = client.get(url, params={"since": since}, headers=headers).json()
    assert resp["total"] == 10
    page2 = client.get(f"{url}?limit=100&offset=200", headers=headers).json()
    assert len(page2["items"]) == 50


def test_export_json_and_pdf(client, enabled_user, fake_platform, queued):
    _, headers = enabled_user
    account_id = _track(client, headers).json()["social_account"]["id"]
    backfill_all(account_id)

    resp = client.get(f"/api/accounts/{account_id}/export?format=json&q=cats", headers=headers)
    assert resp.status_code == 200
    assert 'filename="tabula-bluesky-alice.json"' in resp.headers["content-disposition"]
    data = json.loads(resp.content)
    assert data["account"]["handle"] == "alice"
    assert data["count"] == 25
    assert data["posts"][0]["id"] == "p240"

    resp = client.get(f"/api/accounts/{account_id}/export?format=pdf", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_archive_is_private_to_trackers(client, admin, enabled_user, fake_platform, queued):
    _, headers = enabled_user
    account_id = _track(client, headers).json()["social_account"]["id"]
    other_id, other = register(client, "other@example.com")
    client.patch(f"/api/users/{other_id}", json={"is_enabled": True}, headers=admin)

    assert client.get(f"/api/accounts/{account_id}", headers=other).status_code == 404
    assert client.get(f"/api/accounts/{account_id}/posts", headers=other).status_code == 404
    assert client.get(f"/api/accounts/{account_id}", headers=admin).status_code == 200

    # The second tracker shares the same archive, and no new backfill is queued.
    queued.clear()
    resp = _track(client, other, handle="alice", interval=15)
    assert resp.json()["social_account"]["id"] == account_id
    assert queued == []
    assert client.get(f"/api/accounts/{account_id}", headers=other).status_code == 200

    # Admins can list everyone's trackings; users only see their own.
    assert len(client.get("/api/trackings", headers=other).json()) == 1
    assert len(client.get("/api/trackings?all_users=true", headers=other).json()) == 1
    assert len(client.get("/api/trackings?all_users=true", headers=admin).json()) == 2


def test_schedule_uses_shortest_active_interval(client, admin, enabled_user, fake_platform,
                                                queued):
    _, headers = enabled_user
    mine = _track(client, headers, interval=120).json()
    account_id = mine["social_account"]["id"]
    theirs = _track(client, admin, interval=30).json()
    assert theirs["social_account"]["scan_interval_minutes"] == 30

    client.patch(f"/api/trackings/{theirs['id']}", json={"is_paused": True}, headers=admin)
    account = client.get(f"/api/accounts/{account_id}", headers=headers).json()
    assert account["scan_interval_minutes"] == 120

    client.delete(f"/api/trackings/{mine['id']}", headers=headers)
    account = client.get(f"/api/accounts/{account_id}", headers=admin).json()
    assert account["scan_interval_minutes"] is None
    assert account["next_scan_at"] is None


def test_interval_limits_and_platform_rules(client, admin, enabled_user, fake_platform, queued):
    _, headers = enabled_user
    resp = _track(client, headers, interval=5)
    assert resp.status_code == 422
    assert "15 minutes" in resp.json()["detail"]

    client.patch("/api/settings", json={"enabled_platforms": ["bluesky"]}, headers=admin)
    assert _track(client, headers, platform="x").status_code == 422
    assert _track(client, headers, handle="ghost").status_code == 404

    client.patch("/api/settings", json={"max_trackings_per_user": 1}, headers=admin)
    assert _track(client, headers, handle="https://bsky.app/profile/bob").status_code == 201
    assert _track(client, headers, handle="carol").status_code == 422


def test_scan_now_and_due_scans(client, enabled_user, fake_platform, queued):
    _, headers = enabled_user
    tracking = _track(client, headers).json()
    account_id = tracking["social_account"]["id"]
    queued.clear()
    resp = client.post(f"/api/trackings/{tracking['id']}/scan", headers=headers)
    assert resp.status_code == 202
    assert queued == [("scan", account_id)]

    async def make_due_and_claim():
        async with AsyncSessionLocal() as db:
            account = await db.get(SocialAccount, uuid.UUID(account_id))
            account.next_scan_at = datetime.now(UTC) - timedelta(minutes=1)
            await db.commit()
            first = await scan_service.claim_due_scans(db)
            second = await scan_service.claim_due_scans(db)
            return [str(a.id) for a in first], second

    first, second = asyncio.run(make_due_and_claim())
    assert first == [account_id]
    assert second == []  # claimed once per slot


def test_platform_errors_are_recorded(client, enabled_user, fake_platform, queued):
    from app.platforms import PlatformError

    _, headers = enabled_user
    account_id = _track(client, headers).json()["social_account"]["id"]

    async def boom(profile, cursor=None):
        raise PlatformError("X rejected the API bearer token")

    fake_platform.fetch_page = boom
    assert _run(lambda db, a: scan_service.backfill(db, a, 1), account_id) is False
    account = client.get(f"/api/accounts/{account_id}", headers=headers).json()
    assert account["backfill_status"] == "failed"
    assert "bearer token" in account["last_error"]


def test_scheduler_requeues_forgotten_backfills_once(client, enabled_user, fake_platform, queued):
    _, headers = enabled_user
    account_id = _track(client, headers).json()["social_account"]["id"]

    async def age_and_collect():
        async with AsyncSessionLocal() as db:
            account = await db.get(SocialAccount, uuid.UUID(account_id))
            account.updated_at = datetime.now(UTC) - timedelta(minutes=10)
            await db.commit()
            first = await scan_service.backfills_to_queue(db)
            second = await scan_service.backfills_to_queue(db)
            return [str(a.id) for a in first], second

    first, second = asyncio.run(age_and_collect())
    assert first == [account_id]
    assert second == []
