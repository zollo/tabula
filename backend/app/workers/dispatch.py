"""Queueing work for the Celery workers from the API.

Best-effort: if Redis is unreachable the API call still succeeds, and the beat
scheduler picks the work up anyway (pending backfills and due scans are found
from the database on every tick).

Two queues: `default` for API-based work and `browser` for platforms collected
with a browser (served by the browser worker, which has Chromium).
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from app.models.platform import Platform
from app.platforms.base import PlatformError, ProfileInfo, ProfileNotFound

logger = logging.getLogger("tabula.dispatch")

DEFAULT_QUEUE = "default"
BROWSER_QUEUE = "browser"
# How long adding an account waits for the browser worker to look it up.
RESOLVE_TIMEOUT_SECONDS = 120


def queue_for(source: str) -> str:
    return BROWSER_QUEUE if source == "browser" else DEFAULT_QUEUE


def _send(task: str, account_id: uuid.UUID, queue: str) -> None:
    from app.workers.celery_app import celery_app

    try:
        celery_app.send_task(task, args=[str(account_id)], queue=queue, retry=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not queue %s for %s (%s); the scheduler will retry", task,
                       account_id, exc)


def enqueue_backfill(account_id: uuid.UUID, queue: str = DEFAULT_QUEUE) -> None:
    _send("app.workers.tasks.backfill_account", account_id, queue)


def _resolve_blocking(platform: Platform, handle: str) -> dict:
    from app.workers.celery_app import celery_app

    result = celery_app.send_task(
        "app.workers.tasks.resolve_profile",
        args=[platform.value, handle],
        queue=BROWSER_QUEUE,
        retry=False,
    )
    try:
        return result.get(timeout=RESOLVE_TIMEOUT_SECONDS, propagate=True)
    finally:
        result.forget()


async def resolve_in_browser(platform: Platform, handle: str) -> ProfileInfo:
    """Look a handle up through the browser worker (the API has no browser).

    Raises ProfileNotFound, or a retryable PlatformError when the worker is
    busy, down or unreachable.
    """
    try:
        answer = await asyncio.to_thread(_resolve_blocking, platform, handle)
    except Exception as exc:  # noqa: BLE001  (timeout, broker down, worker crash)
        logger.warning("Browser lookup of %s/%s failed: %s", platform, handle, exc)
        raise PlatformError(
            "The browser worker didn't answer in time; try again in a few minutes",
            retryable=True,
        ) from exc
    if answer.get("not_found"):
        raise ProfileNotFound(answer.get("error") or "Account not found")
    if answer.get("error"):
        raise PlatformError(answer["error"], retryable=bool(answer.get("retryable")))
    return ProfileInfo(**answer["profile"])
