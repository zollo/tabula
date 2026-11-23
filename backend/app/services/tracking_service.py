"""Adding, changing and removing tracked social accounts, and who may see what.

A user sees a social account (and its posts) only while they track it; admins
see everything. Adding an account that nobody tracked before resolves it on the
platform and queues its history backfill.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import platforms
from app.core.errors import Conflict, Forbidden, Invalid, NotFound
from app.models.platform import PLATFORM_LABELS, Platform
from app.models.social_account import BackfillStatus, SocialAccount
from app.models.tracking import Tracking
from app.models.user import User
from app.services import settings_service
from app.workers import dispatch


def normalize_handle(platform: Platform, handle: str) -> str:
    h = handle.strip()
    # Accept pasted profile URLs.
    for prefix in (
        "https://x.com/",
        "https://twitter.com/",
        "https://bsky.app/profile/",
        "https://www.reddit.com/user/",
        "https://www.reddit.com/u/",
        "https://reddit.com/user/",
    ):
        if h.lower().startswith(prefix):
            h = h[len(prefix):].split("/")[0]
    h = h.lstrip("@")
    if platform == Platform.reddit:
        h = h.removeprefix("u/")
    if not h or len(h) > 255 or any(c.isspace() for c in h):
        raise Invalid("Enter a handle such as @someone")
    return h.lower()


async def list_trackings(db: AsyncSession, user: User, all_users: bool = False) -> list[Tracking]:
    query = select(Tracking).order_by(Tracking.created_at.desc())
    if not (all_users and user.is_admin):
        query = query.where(Tracking.user_id == user.id)
    return list((await db.execute(query)).scalars())


async def get_tracking(db: AsyncSession, user: User, tracking_id: uuid.UUID) -> Tracking:
    tracking = await db.get(Tracking, tracking_id)
    if tracking is None or (tracking.user_id != user.id and not user.is_admin):
        raise NotFound("Tracked account not found")
    return tracking


async def get_account(db: AsyncSession, user: User, account_id: uuid.UUID) -> SocialAccount:
    account = await db.get(SocialAccount, account_id)
    if account is None:
        raise NotFound("Account not found")
    if not user.is_admin:
        mine = await db.execute(
            select(Tracking.id).where(
                Tracking.user_id == user.id, Tracking.social_account_id == account_id
            )
        )
        if mine.first() is None:
            raise NotFound("Account not found")
    return account


async def refresh_schedule(db: AsyncSession, account: SocialAccount) -> None:
    """Effective interval = shortest among active trackings; none = not scanned."""
    result = await db.execute(
        select(func.min(Tracking.scan_interval_minutes)).where(
            Tracking.social_account_id == account.id, Tracking.is_paused.is_(False)
        )
    )
    interval = result.scalar_one_or_none()
    account.scan_interval_minutes = interval
    if interval is None:
        account.next_scan_at = None
    else:
        earliest = datetime.now(UTC) + timedelta(minutes=interval)
        if account.next_scan_at is None or _aware(account.next_scan_at) > earliest:
            account.next_scan_at = earliest


def _aware(dt: datetime) -> datetime:
    # SQLite hands back naive datetimes.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _validate_interval(db: AsyncSession, minutes: int | None) -> int:
    cfg = await settings_service.get(db)
    if minutes is None:
        return cfg.default_scan_interval_minutes
    if minutes < cfg.min_scan_interval_minutes:
        raise Invalid(f"Scans can run at most every {cfg.min_scan_interval_minutes} minutes")
    return minutes


async def add_tracking(
    db: AsyncSession, user: User, platform: Platform, handle: str, interval: int | None
) -> Tracking:
    if not user.can_track:
        raise Forbidden(
            "Your account is waiting for an administrator to enable tracking"
        )
    cfg = await settings_service.get(db)
    if platform.value not in cfg.enabled_platforms:
        raise Invalid(f"{PLATFORM_LABELS[platform]} is not enabled on this Tabula")
    interval = await _validate_interval(db, interval)
    if not user.is_admin and cfg.max_trackings_per_user:
        count = await db.execute(
            select(func.count()).select_from(Tracking).where(Tracking.user_id == user.id)
        )
        if count.scalar_one() >= cfg.max_trackings_per_user:
            raise Invalid(f"You can track at most {cfg.max_trackings_per_user} accounts")

    handle = normalize_handle(platform, handle)
    account = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == platform, SocialAccount.handle == handle
            )
        )
    ).scalar_one_or_none()
    is_new = False
    if account is None:
        client = platforms.get_client(platform, settings_service.platform_credentials(cfg))
        try:
            profile = await client.resolve(handle)
        except platforms.ProfileNotFound as exc:
            raise NotFound(str(exc)) from exc
        except platforms.PlatformError as exc:
            raise Invalid(str(exc)) from exc
        # The handle may have changed since we last saw this account.
        account = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.platform == platform,
                    SocialAccount.external_id == profile.external_id,
                )
            )
        ).scalar_one_or_none()
        if account is None:
            is_new = True
            account = SocialAccount(platform=platform, external_id=profile.external_id)
            db.add(account)
        account.handle = profile.handle.lower()
        account.display_name = profile.display_name
        account.avatar_url = profile.avatar_url
        account.profile_url = profile.profile_url
        await db.flush()

    existing = await db.execute(
        select(Tracking).where(
            Tracking.user_id == user.id, Tracking.social_account_id == account.id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise Conflict(f"You already track {account.handle}")

    tracking = Tracking(
        user_id=user.id, social_account_id=account.id, scan_interval_minutes=interval
    )
    db.add(tracking)
    await db.flush()
    await refresh_schedule(db, account)
    await db.commit()
    await db.refresh(tracking)
    if is_new or account.backfill_status == BackfillStatus.failed:
        dispatch.enqueue_backfill(account.id)
    return tracking


async def update_tracking(
    db: AsyncSession, user: User, tracking_id: uuid.UUID, interval: int | None, paused: bool | None
) -> Tracking:
    tracking = await get_tracking(db, user, tracking_id)
    if interval is not None:
        tracking.scan_interval_minutes = await _validate_interval(db, interval)
    if paused is not None:
        tracking.is_paused = paused
    await db.flush()
    await refresh_schedule(db, tracking.social_account)
    await db.commit()
    await db.refresh(tracking)
    return tracking


async def delete_tracking(db: AsyncSession, user: User, tracking_id: uuid.UUID) -> None:
    """Stop tracking. The archive is kept, so tracking again later resumes it."""
    tracking = await get_tracking(db, user, tracking_id)
    account = tracking.social_account
    await db.delete(tracking)
    await db.flush()
    await refresh_schedule(db, account)
    await db.commit()


async def request_scan(db: AsyncSession, user: User, tracking_id: uuid.UUID) -> Tracking:
    if not user.can_track:
        raise Forbidden("Your account is waiting for an administrator to enable tracking")
    tracking = await get_tracking(db, user, tracking_id)
    dispatch.enqueue_scan(tracking.social_account_id)
    return tracking
