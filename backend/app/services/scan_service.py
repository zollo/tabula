"""Fetching posts: incremental scans and resumable history backfills.

Both are called by the Celery tasks in workers/tasks.py with a worker session.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import platforms
from app.models.post import Post
from app.models.social_account import BackfillStatus, SocialAccount
from app.platforms import FetchedPost, ProfileInfo
from app.services import settings_service

logger = logging.getLogger("tabula.scan")

# An incremental scan stops after this many pages even if it never reaches a
# post it already has (the backfill covers anything older).
MAX_SCAN_PAGES = 5
# A backfill marked running without progress for this long is assumed dead.
STALE_BACKFILL = timedelta(minutes=30)
# A pending backfill is re-queued at most this often (the API queues it first).
REQUEUE_PENDING = timedelta(minutes=5)


def _profile(account: SocialAccount) -> ProfileInfo:
    return ProfileInfo(
        external_id=account.external_id,
        handle=account.handle,
        display_name=account.display_name,
        avatar_url=account.avatar_url,
        profile_url=account.profile_url,
    )


async def _client(db: AsyncSession, account: SocialAccount):
    cfg = await settings_service.get(db)
    return platforms.get_client(account.platform, settings_service.platform_credentials(cfg)), cfg


async def store_posts(db: AsyncSession, account: SocialAccount, fetched: list[FetchedPost]) -> int:
    """Insert new posts and refresh metrics on known ones. Returns how many were new."""
    if not fetched:
        return 0
    ids = [p.external_id for p in fetched]
    existing = {
        row.external_id: row
        for row in (
            await db.execute(
                select(Post).where(
                    Post.social_account_id == account.id, Post.external_id.in_(ids)
                )
            )
        ).scalars()
    }
    new = 0
    seen: set[str] = set()
    for p in fetched:
        if p.external_id in seen:
            continue
        seen.add(p.external_id)
        row = existing.get(p.external_id)
        if row is not None:
            row.metrics = p.metrics
            continue
        db.add(
            Post(
                social_account_id=account.id,
                external_id=p.external_id,
                kind=p.kind,
                url=p.url,
                text=p.text,
                posted_at=p.posted_at,
                media=p.media,
                metrics=p.metrics,
                raw=p.raw,
            )
        )
        new += 1
    await db.flush()
    account.post_count = (
        await db.execute(
            select(func.count()).select_from(Post).where(Post.social_account_id == account.id)
        )
    ).scalar_one()
    return new


async def scan(db: AsyncSession, account: SocialAccount) -> int:
    """Fetch posts newer than the archive. Returns how many new posts were stored."""
    now = datetime.now(UTC)
    total = 0
    try:
        client, _ = await _client(db, account)
        cursor = None
        for _ in range(MAX_SCAN_PAGES):
            page = await client.fetch_page(_profile(account), cursor)
            new = await store_posts(db, account, page.posts)
            total += new
            # Stop once a page contained posts we already had.
            if new < len({p.external_id for p in page.posts}) or not page.next_cursor:
                break
            cursor = page.next_cursor
        account.last_error = None
    except platforms.PlatformError as exc:
        account.last_error = str(exc)
        logger.warning("Scan of %s/%s failed: %s", account.platform, account.handle, exc)
    account.last_scanned_at = now
    if account.scan_interval_minutes:
        account.next_scan_at = now + timedelta(minutes=account.scan_interval_minutes)
    await db.commit()
    return total


async def backfill(db: AsyncSession, account: SocialAccount, pages: int) -> bool:
    """Walk back through history for up to `pages` pages.

    Returns True when there is more to fetch (the task re-queues itself).
    """
    now = datetime.now(UTC)
    if account.backfill_status != BackfillStatus.running:
        account.backfill_status = BackfillStatus.running
        account.backfill_started_at = account.backfill_started_at or now
    try:
        client, cfg = await _client(db, account)
        for _ in range(pages):
            page = await client.fetch_page(_profile(account), account.backfill_cursor)
            await store_posts(db, account, page.posts)
            account.backfill_cursor = page.next_cursor
            limit = cfg.backfill_max_posts
            if not page.next_cursor or not page.posts or (limit and account.post_count >= limit):
                account.backfill_status = BackfillStatus.complete
                account.backfill_finished_at = datetime.now(UTC)
                account.backfill_cursor = None
                break
            await db.commit()  # keep progress if the next page fails
        account.last_error = None
    except platforms.PlatformError as exc:
        account.last_error = str(exc)
        if not exc.retryable:
            account.backfill_status = BackfillStatus.failed
        logger.warning("Backfill of %s/%s: %s", account.platform, account.handle, exc)
        await db.commit()
        # Retryable errors leave it `running`; the scheduler re-queues stale ones.
        return False
    account.updated_at = now
    await db.commit()
    return account.backfill_status == BackfillStatus.running


async def claim_due_scans(db: AsyncSession, limit: int = 200) -> list[SocialAccount]:
    """Accounts due for a scan, each pushed to its next slot so it's queued once."""
    now = datetime.now(UTC)
    due = list(
        (
            await db.execute(
                select(SocialAccount)
                .where(
                    SocialAccount.scan_interval_minutes.is_not(None),
                    SocialAccount.next_scan_at <= now,
                )
                .order_by(SocialAccount.next_scan_at)
                .limit(limit)
            )
        ).scalars()
    )
    claimed: list[SocialAccount] = []
    for account in due:
        # Conditional update: if another scheduler moved it already, skip it.
        result = await db.execute(
            update(SocialAccount)
            .where(
                SocialAccount.id == account.id,
                SocialAccount.next_scan_at == account.next_scan_at,
            )
            .values(next_scan_at=now + timedelta(minutes=account.scan_interval_minutes or 60))
        )
        if result.rowcount:
            claimed.append(account)
    await db.commit()
    return claimed


async def backfills_to_queue(db: AsyncSession) -> list[SocialAccount]:
    """Pending backfills nobody picked up, plus running ones that look abandoned.

    Touching `updated_at` on the ones returned keeps the next ticks from
    queueing them again while they wait for a free worker.
    """
    now = datetime.now(UTC)
    query = select(SocialAccount).where(
        or_(
            (SocialAccount.backfill_status == BackfillStatus.pending)
            & (SocialAccount.updated_at < now - REQUEUE_PENDING),
            (SocialAccount.backfill_status == BackfillStatus.running)
            & (SocialAccount.updated_at < now - STALE_BACKFILL),
        )
    )
    accounts = list((await db.execute(query)).scalars())
    if accounts:
        await db.execute(
            update(SocialAccount)
            .where(SocialAccount.id.in_([a.id for a in accounts]))
            .values(updated_at=now)
        )
        await db.commit()
    return accounts
