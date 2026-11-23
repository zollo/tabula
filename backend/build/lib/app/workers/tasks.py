"""Background tasks: the scheduler tick, incremental scans, history backfills,
and (browser worker only) profile lookups.

Each Celery invocation runs its async body in a fresh ``asyncio.run`` with a
short-lived NullPool engine (see db.create_worker_engine).
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid

from app.config import settings
from app.db import create_worker_engine
from app.models.platform import Platform
from app.models.social_account import SocialAccount
from app.platforms.base import PlatformError, ProfileNotFound
from app.services import scan_service, settings_service
from app.workers.celery_app import celery_app
from app.workers.dispatch import queue_for

logger = logging.getLogger("tabula.tasks")


async def _schedule() -> list[tuple[str, str, str]]:
    """(task, account id, queue) for everything due."""
    engine, session_factory = create_worker_engine()
    try:
        async with session_factory() as db:
            cfg = await settings_service.get(db)
            scans = await scan_service.claim_due_scans(db)
            backfills = await scan_service.backfills_to_queue(db)

            def queue(account: SocialAccount) -> str:
                return queue_for(settings_service.platform_source(cfg, account.platform))

            return [("scan", str(a.id), queue(a)) for a in scans] + [
                ("backfill", str(a.id), queue(a)) for a in backfills
            ]
    finally:
        await engine.dispose()


async def _wrong_queue(db, account: SocialAccount, served: str | None) -> str | None:
    """The queue this account's work belongs on, if it isn't the one serving it.

    An admin can switch a platform's source while its work is already queued;
    an API worker has no browser, and a browser worker shouldn't use the API.
    """
    cfg = await settings_service.get(db)
    wanted = queue_for(settings_service.platform_source(cfg, account.platform))
    return wanted if served and served != wanted else None


async def _scan(account_id: str, served: str | None) -> tuple[int, str | None]:
    """(new posts, queue to move to instead)."""
    engine, session_factory = create_worker_engine()
    try:
        async with session_factory() as db:
            account = await db.get(SocialAccount, uuid.UUID(account_id))
            if account is None:
                return 0, None
            if moved := await _wrong_queue(db, account, served):
                return 0, moved
            return await scan_service.scan(db, account), None
    finally:
        await engine.dispose()


async def _backfill(account_id: str, served: str | None) -> tuple[bool, str]:
    """(more to do, queue to continue on)."""
    engine, session_factory = create_worker_engine()
    try:
        async with session_factory() as db:
            account = await db.get(SocialAccount, uuid.UUID(account_id))
            if account is None:
                return False, ""
            if moved := await _wrong_queue(db, account, served):
                return True, moved
            more = await scan_service.backfill(db, account, settings.backfill_pages_per_run)
            cfg = await settings_service.get(db)
            return more, queue_for(settings_service.platform_source(cfg, account.platform))
    finally:
        await engine.dispose()


async def _resolve(platform: str, handle: str) -> dict:
    engine, session_factory = create_worker_engine()
    try:
        async with session_factory() as db:
            cfg = await settings_service.get(db)
            client = await scan_service.client_for(db, Platform(platform), cfg)
            try:
                profile = await client.resolve(handle)
            except ProfileNotFound as exc:
                return {"not_found": True, "error": str(exc)}
            except PlatformError as exc:
                return {"error": str(exc), "retryable": exc.retryable}
            finally:
                await scan_service.close(client)
            return {"profile": dataclasses.asdict(profile)}
    finally:
        await engine.dispose()


@celery_app.task(name="app.workers.tasks.schedule_due_work")
def schedule_due_work() -> dict:
    work = asyncio.run(_schedule())
    for kind, account_id, queue in work:
        task = scan_account if kind == "scan" else backfill_account
        task.apply_async(args=[account_id], queue=queue)
    summary = {
        "scans": sum(1 for w in work if w[0] == "scan"),
        "backfills": sum(1 for w in work if w[0] == "backfill"),
    }
    if work:
        logger.info("Scheduler: %s", summary)
    return summary


def _served_queue(task) -> str | None:
    return (task.request.delivery_info or {}).get("routing_key") or None


@celery_app.task(name="app.workers.tasks.scan_account", bind=True)
def scan_account(self, account_id: str) -> int:
    new, moved = asyncio.run(_scan(account_id, _served_queue(self)))
    if moved:
        scan_account.apply_async(args=[account_id], queue=moved)
    return new


@celery_app.task(name="app.workers.tasks.backfill_account", bind=True)
def backfill_account(self, account_id: str) -> bool:
    more, queue = asyncio.run(_backfill(account_id, _served_queue(self)))
    if more:
        # Chunked so one huge history doesn't hog a worker; resumes from the
        # cursor, on the queue for the platform's current source.
        backfill_account.apply_async(args=[account_id], queue=queue)
    return more


@celery_app.task(name="app.workers.tasks.resolve_profile")
def resolve_profile(platform: str, handle: str) -> dict:
    return asyncio.run(_resolve(platform, handle))
