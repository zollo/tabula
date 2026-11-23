"""Saved browser logins for browser collection: admin CRUD, leasing and pacing.

A browser task *leases* a session (a conditional UPDATE on `lease_token`, so two
workers never get the same one), records each page it loads, and releases it
with the outcome: `ok`, `rate_limited` (cool down, then reuse) or `challenged`
(signed out or blocked: parked as `needs_attention` until an admin replaces it).
Tabula never tries to get around a challenge.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import false, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Invalid, NotFound
from app.core.security import SecretDecryptionError, decrypt_secret, encrypt_secret
from app.models.platform import PLATFORM_LABELS, Platform
from app.models.scraper_session import ScraperSession, SessionStatus
from app.platforms.base import PlatformError

# A lease outlives a crashed worker by at most this long. It's renewed before
# every page load, so it must exceed the longest pacing wait plus one page load
# (schemas.settings caps the wait at 300 seconds).
LEASE_TTL = timedelta(minutes=10)
COOLDOWN = timedelta(minutes=15)
# Signed-in cookie each site sets; a storage state without it isn't a login.
LOGIN_COOKIES = {Platform.x: "auth_token", Platform.reddit: "reddit_session"}
BROKEN = (SessionStatus.needs_attention, SessionStatus.disabled)


class NoSessionAvailable(PlatformError):
    """Every login is busy, paced, cooling down or over its daily cap."""

    def __init__(self, message: str):
        super().__init__(message, retryable=True)


@dataclass
class Lease:
    session_id: uuid.UUID
    token: str
    label: str
    storage_state: dict[str, Any] | None


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes.
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def parse_storage_state(platform: Platform, raw: str | None) -> dict[str, Any] | None:
    """Validate an uploaded Playwright storage state. None = signed out (Reddit only)."""
    if raw is None or not raw.strip():
        if platform == Platform.x:
            raise Invalid("X needs a signed-in browser session")
        return None
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Invalid("The session file isn't valid JSON") from exc
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
        raise Invalid("The session file must be a Playwright storage state with cookies")
    names = {c.get("name") for c in state["cookies"] if isinstance(c, dict)}
    if LOGIN_COOKIES[platform] not in names:
        raise Invalid(f"The session file isn't signed in to {PLATFORM_LABELS[platform]}")
    return state


async def list_sessions(db: AsyncSession) -> list[ScraperSession]:
    query = select(ScraperSession).order_by(ScraperSession.platform, ScraperSession.created_at)
    return list((await db.execute(query)).scalars())


async def _get(db: AsyncSession, session_id: uuid.UUID) -> ScraperSession:
    row = await db.get(ScraperSession, session_id)
    if row is None:
        raise NotFound("Browser session not found")
    return row


async def create_session(
    db: AsyncSession, platform: Platform, label: str, storage_state: str | None
) -> ScraperSession:
    from app.services.settings_service import BROWSER_PLATFORMS

    if platform not in BROWSER_PLATFORMS:
        raise Invalid(f"{PLATFORM_LABELS[platform]} doesn't use browser sessions")
    label = label.strip()
    if not label:
        raise Invalid("Give the session a name")
    state = parse_storage_state(platform, storage_state)
    row = ScraperSession(
        platform=platform,
        label=label,
        storage_state=encrypt_secret(json.dumps(state)) if state else None,
        status=SessionStatus.active,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    label: str | None = None,
    enabled: bool | None = None,
    storage_state: str | None = None,
) -> ScraperSession:
    """Rename, turn on/off, or replace the login. Turning on clears any error."""
    row = await _get(db, session_id)
    if label is not None:
        if not label.strip():
            raise Invalid("Give the session a name")
        row.label = label.strip()
    if storage_state is not None:
        state = parse_storage_state(row.platform, storage_state)
        row.storage_state = encrypt_secret(json.dumps(state)) if state else None
        enabled = True if enabled is None else enabled
    if enabled is not None:
        if enabled:
            row.status = SessionStatus.active
            row.last_error = None
            row.cooldown_until = None
        else:
            row.status = SessionStatus.disabled
    await db.commit()
    await db.refresh(row)
    return row


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.delete(await _get(db, session_id))
    await db.commit()


async def has_usable_session(db: AsyncSession, platform: Platform) -> bool:
    query = select(ScraperSession.id).where(
        ScraperSession.platform == platform, ScraperSession.status.not_in(BROKEN)
    )
    return (await db.execute(query.limit(1))).first() is not None



def _decrypt_state(row: ScraperSession) -> dict[str, Any] | None:
    if not row.storage_state:
        return None
    try:
        return json.loads(decrypt_secret(row.storage_state))
    except (SecretDecryptionError, json.JSONDecodeError) as exc:
        raise ValueError("unreadable") from exc


async def lease(db: AsyncSession, platform: Platform, daily_cap: int) -> Lease:
    """Lease the least recently used free session, or raise NoSessionAvailable."""
    now = _now()
    today = _today()
    rows = list(
        (
            await db.execute(
                select(ScraperSession)
                .where(
                    ScraperSession.platform == platform,
                    ScraperSession.status.not_in(BROKEN),
                )
                .order_by(ScraperSession.last_used_at.is_not(None), ScraperSession.last_used_at)
            )
        ).scalars()
    )
    label = PLATFORM_LABELS[platform]
    if not rows:
        raise NoSessionAvailable(f"No working {label} browser session; add one in Settings")
    soonest: datetime | None = None
    for row in rows:
        waits = [
            t
            for t in (
                _aware(row.leased_until),
                _aware(row.next_allowed_at),
                _aware(row.cooldown_until) if row.status == SessionStatus.cooling_down else None,
            )
            if t is not None and t > now
        ]
        if waits:
            free_at = max(waits)
            soonest = free_at if soonest is None else min(soonest, free_at)
            continue
        if daily_cap and row.pages_day == today and row.pages_today >= daily_cap:
            continue
        try:
            state = _decrypt_state(row)
        except ValueError:
            row.status = SessionStatus.needs_attention
            row.last_error = "Saved login can't be decrypted (SECRET_KEY changed?)"
            await db.commit()
            continue
        token = uuid.uuid4().hex
        seen = row.lease_token
        # Re-check eligibility in the UPDATE itself: another worker may have
        # leased and released it (token back to NULL) with a new cooldown,
        # pacing delay, page count or status since we read it.
        result = await db.execute(
            update(ScraperSession)
            .where(
                ScraperSession.id == row.id,
                ScraperSession.lease_token.is_(None)
                if seen is None
                else ScraperSession.lease_token == seen,
                ScraperSession.status.not_in(BROKEN),
                or_(ScraperSession.leased_until.is_(None), ScraperSession.leased_until <= now),
                or_(
                    ScraperSession.next_allowed_at.is_(None),
                    ScraperSession.next_allowed_at <= now,
                ),
                or_(
                    ScraperSession.status != SessionStatus.cooling_down,
                    ScraperSession.cooldown_until.is_(None),
                    ScraperSession.cooldown_until <= now,
                ),
                or_(
                    true() if not daily_cap else false(),
                    ScraperSession.pages_day.is_(None),
                    ScraperSession.pages_day != today,
                    ScraperSession.pages_today < daily_cap,
                ),
            )
            .values(
                lease_token=token,
                leased_until=now + LEASE_TTL,
                status=SessionStatus.active,
                cooldown_until=None,
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        if result.rowcount:
            return Lease(session_id=row.id, token=token, label=row.label, storage_state=state)
    when = f" (next free at {soonest:%H:%M} UTC)" if soonest else " (daily page cap reached)"
    raise NoSessionAvailable(f"All {label} browser sessions are busy{when}")


async def _owned(db: AsyncSession, lease_: Lease) -> ScraperSession | None:
    row = await db.get(ScraperSession, lease_.session_id, populate_existing=True)
    return row if row is not None and row.lease_token == lease_.token else None


async def start_page(db: AsyncSession, lease_: Lease, daily_cap: int) -> None:
    """Before each page load: confirm the lease is still ours, take one page of
    today's allowance, and renew the lease. Raises (retryable) if the lease was
    lost or the daily cap is used up, so the caller stops with its cursor kept.
    """
    row = await _owned(db, lease_)
    now = _now()
    if row is None:  # another worker took it over after our lease expired
        raise NoSessionAvailable(f"Lost the lease on browser session {lease_.label!r}")
    today = _today()
    if row.pages_day != today:
        row.pages_day = today
        row.pages_today = 0
    if daily_cap and row.pages_today >= daily_cap:
        raise NoSessionAvailable(
            f"Browser session {lease_.label!r} reached its daily page cap"
        )
    row.pages_today += 1
    row.pages_total += 1
    row.last_used_at = now
    row.leased_until = now + LEASE_TTL
    await db.commit()


async def release(
    db: AsyncSession,
    lease_: Lease,
    *,
    outcome: str = "ok",
    error: str | None = None,
    interval_seconds: int = 0,
) -> None:
    row = await _owned(db, lease_)
    if row is None:
        return
    now = _now()
    row.lease_token = None
    row.leased_until = None
    row.next_allowed_at = now + timedelta(seconds=interval_seconds)
    if outcome == "rate_limited":
        row.status = SessionStatus.cooling_down
        row.cooldown_until = now + COOLDOWN
        row.last_error = error or "Rate limited"
    elif outcome == "challenged":
        row.status = SessionStatus.needs_attention
        row.last_error = error or "Signed out or challenged; replace this session"
    elif outcome == "error":
        row.last_error = error
    else:
        row.last_error = None
    await db.commit()


class Broker:
    """What a browser client uses to lease, count and release a login."""

    def __init__(self, db: AsyncSession, platform: Platform, *, interval_seconds: int,
                 daily_cap: int):
        self.db = db
        self.platform = platform
        self.interval_seconds = interval_seconds
        self.daily_cap = daily_cap
        self.lease: Lease | None = None

    async def acquire(self) -> dict[str, Any] | None:
        self.lease = await lease(self.db, self.platform, self.daily_cap)
        return self.lease.storage_state

    async def start_page(self) -> None:
        if self.lease is not None:
            await start_page(self.db, self.lease, self.daily_cap)

    async def release(self, outcome: str, error: str | None) -> None:
        if self.lease is not None:
            await release(
                self.db,
                self.lease,
                outcome=outcome,
                error=error,
                interval_seconds=self.interval_seconds,
            )
            self.lease = None
