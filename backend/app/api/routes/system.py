"""System settings (admins) and the tracking options every user needs."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user
from app.db import get_db
from app.models.platform import PLATFORM_LABELS, Platform
from app.models.system_settings import SystemSettings
from app.models.user import User
from app.schemas.settings import (
    PlatformOption,
    SystemSettingsRead,
    SystemSettingsUpdate,
    TrackingOptions,
)
from app.services import settings_service

router = APIRouter(tags=["settings"])


def _read(row: SystemSettings) -> SystemSettingsRead:
    creds = settings_service.platform_credentials(row)
    return SystemSettingsRead(
        allow_registration=row.allow_registration,
        enabled_platforms=[Platform(p) for p in row.enabled_platforms],
        min_scan_interval_minutes=row.min_scan_interval_minutes,
        default_scan_interval_minutes=row.default_scan_interval_minutes,
        max_trackings_per_user=row.max_trackings_per_user,
        backfill_max_posts=row.backfill_max_posts,
        credentials=settings_service.credential_sources(row),
        credential_values={
            k: creds[k] for k, secret in settings_service.CREDENTIAL_FIELDS.items() if not secret
        },
        platform_sources={p: settings_service.platform_source(row, p) for p in Platform},
        browser_platforms=list(settings_service.BROWSER_PLATFORMS),
        public_platforms=list(settings_service.PUBLIC_PLATFORMS),
        archive_platforms=list(settings_service.ARCHIVE_PLATFORMS),
        browser_page_interval_seconds=row.browser_page_interval_seconds,
        browser_daily_page_cap=row.browser_daily_page_cap,
        public_request_interval_seconds=row.public_request_interval_seconds,
    )


@router.get("/settings", response_model=SystemSettingsRead, operation_id="get_system_settings")
async def get_system_settings(
    _: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> SystemSettingsRead:
    return _read(await settings_service.get(db))


@router.patch(
    "/settings", response_model=SystemSettingsRead, operation_id="update_system_settings"
)
async def update_system_settings(
    payload: SystemSettingsUpdate,
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> SystemSettingsRead:
    changes = payload.model_dump(exclude_unset=True, exclude={"credentials"})
    row = await settings_service.update(db, changes, payload.credentials)
    return _read(row)


@router.get("/options", response_model=TrackingOptions, operation_id="get_tracking_options")
async def get_tracking_options(
    _: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> TrackingOptions:
    """Platforms users may track and the scan-frequency limits."""
    row = await settings_service.get(db)
    return TrackingOptions(
        platforms=[
            PlatformOption(
                value=p,
                label=PLATFORM_LABELS[p],
                enabled=p.value in row.enabled_platforms,
                configured=await settings_service.platform_ready(db, row, p),
            )
            for p in Platform
        ],
        min_scan_interval_minutes=row.min_scan_interval_minutes,
        default_scan_interval_minutes=row.default_scan_interval_minutes,
        max_trackings_per_user=row.max_trackings_per_user,
    )
