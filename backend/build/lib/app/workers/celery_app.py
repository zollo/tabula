"""Celery application + beat schedule for scans and backfills."""
from __future__ import annotations

from celery import Celery

from app.config import settings

celery_app = Celery("tabula", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    result_expires=3600,
    # Fail fast when the API publishes and Redis is down (dispatch.py logs it).
    broker_connection_timeout=3,
    # API-based work goes to `default`; browser collection and lookups go to
    # `browser` (see dispatch.queue_for), served by the browser worker.
    task_default_queue="default",
    # Browser tasks are slow; don't let one process hoard several of them.
    worker_prefetch_multiplier=1,
    # Every tick, queue the accounts that are due for a scan, and any backfill
    # that is pending or looks abandoned.
    beat_schedule={
        "schedule-due-work": {
            "task": "app.workers.tasks.schedule_due_work",
            "schedule": float(settings.scheduler_tick_seconds),
        }
    },
)

celery_app.autodiscover_tasks(["app.workers"])

from app.workers import tasks  # noqa: E402,F401  (register tasks)
