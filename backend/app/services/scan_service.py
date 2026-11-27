"""Fetching posts: incremental scans and resumable history backfills.

Both are called by the Celery tasks in workers/tasks.py with a worker session.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
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


async def client_for(db: AsyncSession, platform, cfg, *, max_wait: timedelta | None = None):
    """The API, browser, public or archive client for `platform`, per the admin's
    source setting.

    Browser clients lease a saved login through `db`; close them with `close`.
    Public and archive clients pace every request through a pacer shared by all
    workers (it commits `db` while claiming a slot).
    """
    source = settings_service.platform_source(cfg, platform)
    if source == "archive":
        from app.platforms.reddit_archive import REQUEST_INTERVAL_SECONDS
        from app.services.pacer_service import Pacer

        pacer = Pacer(
            db,
            f"{platform.value}-archive",
            REQUEST_INTERVAL_SECONDS,
            label=f"{platform.value} archive",
            **({"max_wait": max_wait} if max_wait is not None else {}),
        )
        creds = settings_service.platform_credentials(cfg)
        user_agent = creds.get(f"{platform.value}_user_agent", "")
        return platforms.get_archive_client(platform, pacer, user_agent)
    if source == "public":
        from app.services.pacer_service import Pacer

        pacer = Pacer(
            db,
            f"{platform.value}-public",
            cfg.public_request_interval_seconds,
            label=f"{platform.value} public pages",
            **({"max_wait": max_wait} if max_wait is not None else {}),
        )
        creds = settings_service.platform_credentials(cfg)
        user_agent = creds.get(f"{platform.value}_user_agent", "")
        return platforms.get_public_client(platform, pacer, user_agent)
    if source == "browser":
        from app.services.scraper_session_service import Broker

        broker = Broker(
            db,
            platform,
            interval_seconds=cfg.browser_page_interval_seconds,
            daily_cap=cfg.browser_daily_page_cap,
        )
        return platforms.get_browser_client(platform, broker)
    return platforms.get_client(platform, settings_service.platform_credentials(cfg))


async def close(client) -> None:
    """Release a browser client's login and browser (no-op for API clients)."""
    aclose = getattr(client, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception:  # noqa: BLE001
            logger.warning("Closing the %s client failed", client.platform, exc_info=True)


async def _client(db: AsyncSession, account: SocialAccount):
    cfg = await settings_service.get(db)
    return await client_for(db, account.platform, cfg), cfg


async def store_posts(db: AsyncSession, account: SocialAccount, fetched: list[FetchedPost]) -> int:
    """Insert new posts and refresh metrics on known ones. Returns how many were new.

    A scan and a backfill of the same account can overlap, so each insert runs in
    a savepoint: if the other writer stored the post first, it's skipped.
    """
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
        try:
            async with db.begin_nested():
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
        except IntegrityError:
            pass  # stored concurrently by another scan/backfill
    await db.flush()
    account.post_count = (
        await db.execute(
            select(func.count()).select_from(Post).where(Post.social_account_id == account.id)
        )
    ).scalar_one()
    return new


async def _keepable(db: AsyncSession, account: SocialAccount, source, cursor):
    """`cursor`, unless the admin switched sources since the scan started (the
    switch cleared it and scheduled a backfill; don't bring it back)."""
    if cursor is None or source is None:
        return None
    if await settings_service.current_source(db, account.platform) != source:
        return None
    return cursor


async def scan(db: AsyncSession, account: SocialAccount) -> int:
    """Fetch posts newer than the archive. Returns how many new posts were stored."""
    now = datetime.now(UTC)
    total = 0
    client = None
    retry_at = None
    cursor = None
    source = None
    try:
        client, cfg = await _client(db, account)
        source = settings_service.platform_source(cfg, account.platform)
        # A scan our own pacing deferred continues where it stopped.
        cursor = account.scan_cursor
        for _ in range(MAX_SCAN_PAGES):
            page = await client.fetch_page(_profile(account), cursor)
            new = await store_posts(db, account, page.posts)
            total += new
            # Stop once a page contained posts we already had.
            if new < len({p.external_id for p in page.posts}) or not page.next_cursor:
                break
            cursor = page.next_cursor
        account.scan_cursor = None
        account.last_error = None
    except platforms.Throttled as exc:
        # Our own pacing is full: not a failure, just come back when it frees up.
        retry_at = exc.retry_at
        account.scan_cursor = await _keepable(db, account, source, cursor)
        logger.info("Scan of %s/%s deferred: %s", account.platform, account.handle, exc)
    except platforms.PlatformError as exc:
        # Retried on the next scan from the same page; a hard failure starts over.
        account.scan_cursor = (
            await _keepable(db, account, source, cursor) if exc.retryable else None
        )
        account.last_error = str(exc)
        logger.warning("Scan of %s/%s failed: %s", account.platform, account.handle, exc)
    finally:
        if client is not None:
            await close(client)
    if retry_at is not None:
        # Pages fetched before the deferral are stored; the rest waits for the
        # slot. Also for paused accounts, so a "scan now" isn't lost: the
        # scheduler clears next_scan_at again once that scan has run.
        account.next_scan_at = retry_at
        await db.commit()
        return total
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
    account.backfill_touched_at = now
    client = None
    try:
        client, cfg = await _client(db, account)
        source = settings_service.platform_source(cfg, account.platform)
        for _ in range(pages):
            page = await client.fetch_page(_profile(account), account.backfill_cursor)
            await store_posts(db, account, page.posts)
            if await settings_service.current_source(db, account.platform) != source:
                # The admin switched sources mid-run: this client's cursor means
                # nothing to the other one, and the backfill was restarted.
                await db.commit()
                return False
            account.backfill_cursor = page.next_cursor
            limit = cfg.backfill_max_posts
            if not page.next_cursor or not page.posts or (limit and account.post_count >= limit):
                account.backfill_status = BackfillStatus.complete
                account.backfill_finished_at = datetime.now(UTC)
                account.backfill_cursor = None
                break
            account.backfill_touched_at = datetime.now(UTC)
            await db.commit()  # keep progress if the next page fails
        account.last_error = None
    except platforms.Throttled as exc:
        # Hand it back to the scheduler instead of holding a worker: a pending
        # backfill nobody touched is queued again on the next tick after the slot.
        account.backfill_status = BackfillStatus.pending
        account.backfill_touched_at = exc.retry_at - REQUEUE_PENDING
        logger.info("Backfill of %s/%s deferred: %s", account.platform, account.handle, exc)
        await db.commit()
        return False
    except platforms.PlatformError as exc:
        account.last_error = str(exc)
        if not exc.retryable:
            account.backfill_status = BackfillStatus.failed
        logger.warning("Backfill of %s/%s: %s", account.platform, account.handle, exc)
        await db.commit()
        # Retryable errors leave it `running`; the scheduler re-queues stale ones.
        return False
    finally:
        if client is not None:
            await close(client)
    await db.commit()
    return account.backfill_status == BackfillStatus.running


async def claim_due_scans(db: AsyncSession, limit: int = 200) -> list[SocialAccount]:
    """Accounts due for a scan, each pushed to its next slot so it's queued once.

    `next_scan_at` is also how "scan now" is requested (see
    tracking_service.request_scan), so a paused account can still be due once.
    """
    now = datetime.now(UTC)
    due = list(
        (
            await db.execute(
                select(SocialAccount)
                .where(SocialAccount.next_scan_at <= now)
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
            .values(
                next_scan_at=(
                    now + timedelta(minutes=account.scan_interval_minutes)
                    if account.scan_interval_minutes
                    else None
                )
            )
        )
        if result.rowcount:
            claimed.append(account)
    await db.commit()
    return claimed


async def backfills_to_queue(db: AsyncSession) -> list[SocialAccount]:
    """Pending backfills nobody picked up, plus running ones that look abandoned.

    Touching `backfill_touched_at` on the ones returned keeps the next ticks
    from queueing them again while they wait for a free worker.
    """
    now = datetime.now(UTC)
    query = select(SocialAccount).where(
        or_(
            (SocialAccount.backfill_status == BackfillStatus.pending)
            & (
                SocialAccount.backfill_touched_at.is_(None)
                | (SocialAccount.backfill_touched_at < now - REQUEUE_PENDING)
            ),
            (SocialAccount.backfill_status == BackfillStatus.running)
            & (
                SocialAccount.backfill_touched_at.is_(None)
                | (SocialAccount.backfill_touched_at < now - STALE_BACKFILL)
            ),
        )
    )
    accounts = list((await db.execute(query)).scalars())
    if accounts:
        await db.execute(
            update(SocialAccount)
            .where(SocialAccount.id.in_([a.id for a in accounts]))
            .values(backfill_touched_at=now)
        )
        await db.commit()
    return accounts
