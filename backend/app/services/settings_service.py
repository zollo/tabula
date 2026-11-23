"""System settings (a singleton row) and the platform credentials in effect.

Platform credentials come from env vars by default; an admin can override each
one from System settings, where it is stored Fernet-encrypted. The row is read
on every call so the API and the worker pick up changes without a restart.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import Invalid
from app.core.security import SecretDecryptionError, decrypt_secret, encrypt_secret
from app.models.platform import Platform
from app.models.system_settings import SINGLETON_ID, SystemSettings

logger = logging.getLogger("tabula.settings")

# Credential keys an admin may set, and whether each is a secret.
CREDENTIAL_FIELDS: dict[str, bool] = {
    "x_bearer_token": True,
    "reddit_client_id": False,
    "reddit_client_secret": True,
    "reddit_user_agent": False,
    "bluesky_appview_url": False,
}


async def get(db: AsyncSession) -> SystemSettings:
    row = await db.get(SystemSettings, SINGLETON_ID)
    if row is None:
        row = SystemSettings(
            id=SINGLETON_ID,
            allow_registration=settings.allow_registration,
            enabled_platforms=[p.value for p in Platform],
            min_scan_interval_minutes=15,
            default_scan_interval_minutes=60,
            max_trackings_per_user=25,
            backfill_max_posts=5000,
            credentials={},
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


def _env_credentials() -> dict[str, str]:
    return {key: str(getattr(settings, key) or "") for key in CREDENTIAL_FIELDS}


def platform_credentials(row: SystemSettings) -> dict[str, str]:
    """Env defaults, overridden by whatever an admin saved."""
    creds = _env_credentials()
    for key, stored in (row.credentials or {}).items():
        if key not in CREDENTIAL_FIELDS or not stored:
            continue
        try:
            creds[key] = decrypt_secret(stored)
        except SecretDecryptionError:
            logger.warning("Saved %s can't be decrypted (SECRET_KEY rotated?); using env", key)
    return creds


def credential_sources(row: SystemSettings) -> dict[str, str]:
    """For each credential: 'saved', 'environment' or 'unset' (values never leave)."""
    env = _env_credentials()
    out: dict[str, str] = {}
    for key in CREDENTIAL_FIELDS:
        if (row.credentials or {}).get(key):
            out[key] = "saved"
        elif env[key]:
            out[key] = "environment"
        else:
            out[key] = "unset"
    return out


def platform_configured(platform: Platform, creds: dict[str, str]) -> bool:
    if platform == Platform.x:
        return bool(creds["x_bearer_token"])
    if platform == Platform.reddit:
        return bool(creds["reddit_client_id"] and creds["reddit_client_secret"])
    return bool(creds["bluesky_appview_url"])


async def update(
    db: AsyncSession, changes: dict, credentials: dict[str, str | None]
) -> SystemSettings:
    """Apply plain field changes, then credentials (None or "" clears a saved value)."""
    row = await get(db)
    if "enabled_platforms" in changes and changes["enabled_platforms"] is not None:
        changes["enabled_platforms"] = [Platform(p).value for p in changes["enabled_platforms"]]
    for key, value in changes.items():
        if value is not None:
            setattr(row, key, value)
    if row.default_scan_interval_minutes < row.min_scan_interval_minutes:
        raise Invalid("The default scan interval can't be shorter than the minimum")
    if credentials:
        saved = dict(row.credentials or {})
        for key, value in credentials.items():
            if key not in CREDENTIAL_FIELDS:
                raise Invalid(f"Unknown credential {key!r}")
            if value:
                saved[key] = encrypt_secret(value.strip())
            else:
                saved.pop(key, None)
        row.credentials = saved
    await db.commit()
    await db.refresh(row)
    return row
