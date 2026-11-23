"""Smoke test: the FastAPI app builds, its schema generates, and the worker imports."""
from __future__ import annotations


def test_app_and_openapi_build():
    from app.main import app

    assert app.title == "Tabula"
    paths = app.openapi()["paths"]
    assert "/api/health" in paths
    assert "/api/trackings" in paths
    assert "/api/accounts/{account_id}/export" in paths
    assert "/api/settings" in paths
    # Starting Google sign-in works anonymously; only linking needs a session.
    assert {} in paths["/api/auth/google/authorize"]["post"]["security"]


def test_worker_registers_tasks():
    from app.workers.celery_app import celery_app

    names = set(celery_app.tasks)
    assert {
        "app.workers.tasks.schedule_due_work",
        "app.workers.tasks.scan_account",
        "app.workers.tasks.backfill_account",
    } <= names
    assert "schedule-due-work" in celery_app.conf.beat_schedule


def test_production_refuses_placeholder_secret():
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(TABULA_ENV="production", SECRET_KEY="change-me")
    assert Settings(TABULA_ENV="production", SECRET_KEY="x" * 64).env == "production"
