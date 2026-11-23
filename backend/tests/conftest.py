"""Test fixtures: the real app against a throwaway SQLite database."""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC, datetime, timedelta

# Must be set before the app (and its settings/engine) is imported.
_DB_DIR = tempfile.mkdtemp(prefix="tabula-test-")
# TABULA_TEST_DATABASE_URL runs the suite against a real Postgres instead.
os.environ["DATABASE_URL"] = os.environ.get(
    "TABULA_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_DB_DIR}/test.db"
)
os.environ["DB_NULL_POOL"] = "true"
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@example.com"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "admin-password"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"
# Never talk to a real platform from tests.
for _key in ("X_BEARER_TOKEN", "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"):
    os.environ[_key] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.models  # noqa: E402,F401
from app import platforms  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.post import PostKind  # noqa: E402
from app.platforms import FetchedPost, Page, ProfileInfo, ProfileNotFound  # noqa: E402
from app.workers import dispatch  # noqa: E402


async def _reset_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture
def client():
    asyncio.run(_reset_db())
    # Entering the context runs the lifespan, which bootstraps the admin.
    with TestClient(app) as c:
        yield c


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def admin(client) -> dict[str, str]:
    """Auth headers for the bootstrapped admin."""
    return login(client, "admin@example.com", "admin-password")


def register(client: TestClient, email: str = "user@example.com") -> tuple[str, dict[str, str]]:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "user-password", "display_name": "User"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["user"]["id"], {"Authorization": f"Bearer {body['access_token']}"}


@pytest.fixture
def user(client) -> tuple[str, dict[str, str]]:
    """A self-registered user who has NOT been enabled yet: (id, auth headers)."""
    return register(client)


@pytest.fixture
def enabled_user(client, admin, user) -> tuple[str, dict[str, str]]:
    user_id, headers = user
    resp = client.patch(f"/api/users/{user_id}", json={"is_enabled": True}, headers=admin)
    assert resp.status_code == 200, resp.text
    return user_id, headers


class FakeClient:
    """A platform with `count` posts, one per hour, paged newest first."""

    def __init__(self, platform, count: int = 250, page_size: int = 100):
        self.platform = platform
        self.page_size = page_size
        self.start = datetime(2026, 1, 1, tzinfo=UTC)
        self.posts = [self._post(i) for i in range(count)]
        self.calls = 0

    def _post(self, i: int) -> FetchedPost:
        return FetchedPost(
            external_id=f"p{i}",
            posted_at=self.start + timedelta(hours=i),
            text=f"post number {i}" + (" about cats" if i % 10 == 0 else ""),
            kind=PostKind.reply if i % 7 == 0 else PostKind.post,
            url=f"https://example.com/p{i}",
            metrics={"likes": i},
        )

    def add(self, n: int) -> None:
        start = len(self.posts)
        self.posts += [self._post(i) for i in range(start, start + n)]

    async def resolve(self, handle: str) -> ProfileInfo:
        if handle == "ghost":
            raise ProfileNotFound("not found")
        return ProfileInfo(
            external_id=f"id-{handle}",
            handle=handle,
            display_name=handle.title(),
            profile_url=f"https://example.com/{handle}",
        )

    async def fetch_page(self, profile: ProfileInfo, cursor: str | None = None) -> Page:
        self.calls += 1
        newest_first = list(reversed(self.posts))
        start = int(cursor or 0)
        chunk = newest_first[start : start + self.page_size]
        more = start + self.page_size < len(newest_first)
        return Page(posts=chunk, next_cursor=str(start + self.page_size) if more else None)


@pytest.fixture
def fake_platform(monkeypatch):
    """Every platform is served by one FakeClient; returns it."""
    fake = FakeClient(None)

    def get_client(platform, creds):
        fake.platform = platform
        return fake

    monkeypatch.setattr(platforms, "get_client", get_client)
    return fake


@pytest.fixture
def queued(monkeypatch) -> list[tuple[str, str]]:
    """Captures work the API queues for the worker instead of sending it to Redis."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(dispatch, "enqueue_backfill", lambda i: sent.append(("backfill", str(i))))
    return sent
