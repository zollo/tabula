"""Background tasks: the scheduler tick, incremental scans and history backfills.

Each Celery invocation runs its async body in a fresh ``asyncio.run`` with a
short-lived NullPool engine (see db.create_worker_engine).
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from app.config import settings
from app.db import create_worker_engine
from app.models.social_account import SocialAccount
from app.services import scan_service
from app.workers.celery_app import celery_app

logger = logging.getLogger("tabula.tasks")


async def _schedule() -> dict:
    engine, session_factory = create_worker_engine()
    try:
        async with session_factory() as db:
            scans = await scan_service.claim_due_scans(db)
            backfills = await scan_service.backfills_to_queue(db)
    finally:
        await engine.dispose()
    for account in scans:
        scan_account.delay(str(account.id))
    for account in backfills:
        backfill_account.delay(str(account.id))
    return {"scans": len(scans), "backfills": len(backfills)}


async def _scan(account_id: str) -> int:
    engine, session_factory = create_worker_engine()
    try:
        async with session_factory() as db:
            account = await db.get(SocialAccount, uuid.UUID(account_id))
            if account is None:
                return 0
            return await scan_service.scan(db, account)
    finally:
        await engine.dispose()


async def _backfill(account_id: str) -> bool:
    engine, session_factory = create_worker_engine()
    try:
        async with session_factory() as db:
            account = await db.get(SocialAccount, uuid.UUID(account_id))
            if account is None:
                return False
            return await scan_service.backfill(db, account, settings.backfill_pages_per_run)
    finally:
        await engine.dispose()


@celery_app.task(name="app.workers.tasks.schedule_due_work")
def schedule_due_work() -> dict:
    summary = asyncio.run(_schedule())
    if summary["scans"] or summary["backfills"]:
        logger.info("Scheduler: %s", summary)
    return summary


@celery_app.task(name="app.workers.tasks.scan_account")
def scan_account(account_id: str) -> int:
    return asyncio.run(_scan(account_id))


@celery_app.task(name="app.workers.tasks.backfill_account")
def backfill_account(account_id: str) -> bool:
    more = asyncio.run(_backfill(account_id))
    if more:
        # Chunked so one huge history doesn't hog a worker; resumes from the cursor.
        backfill_account.delay(account_id)
    return more
