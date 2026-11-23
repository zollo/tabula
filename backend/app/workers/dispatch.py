"""Queueing work for the Celery worker from the API.

Best-effort: if Redis is unreachable the API call still succeeds, and the beat
scheduler picks the work up anyway (pending backfills and due scans are found
from the database on every tick).
"""
from __future__ import annotations

import logging
import uuid

logger = logging.getLogger("tabula.dispatch")


def _send(task: str, account_id: uuid.UUID) -> None:
    from app.workers.celery_app import celery_app

    try:
        celery_app.send_task(task, args=[str(account_id)], retry=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not queue %s for %s (%s); the scheduler will retry", task,
                       account_id, exc)


def enqueue_backfill(account_id: uuid.UUID) -> None:
    _send("app.workers.tasks.backfill_account", account_id)


def enqueue_scan(account_id: uuid.UUID) -> None:
    _send("app.workers.tasks.scan_account", account_id)
