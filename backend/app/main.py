"""Tabula FastAPI application: REST API at /api."""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.config import settings
from app.core.errors import TabulaError
from app.db import AsyncSessionLocal
from app.models.user import UserRole
from app.services import user_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tabula")

DESCRIPTION = """\
Tabula archives the posts of social media accounts (X, Bluesky, Reddit) that
its users choose to track, and lets them browse and export the archive.

* Authenticate with `Authorization: Bearer <token>`: a session token from
  `POST /api/auth/login` or Google sign-in.
* Self-registered users must be enabled by an admin before they can track.
"""


async def _bootstrap_admin() -> None:
    """Create the initial admin account if it does not exist yet."""
    async with AsyncSessionLocal() as db:
        if await user_service.get_by_email(db, settings.bootstrap_admin_email):
            return
        password = settings.bootstrap_admin_password or secrets.token_urlsafe(12)
        await user_service.create_user(
            db,
            email=settings.bootstrap_admin_email,
            password=password,
            display_name="Admin",
            role=UserRole.admin,
        )
        if settings.bootstrap_admin_password:
            logger.info("Bootstrapped admin %s", settings.bootstrap_admin_email)
        else:
            logger.warning(
                "Bootstrapped admin %s with generated password %s "
                "(set BOOTSTRAP_ADMIN_PASSWORD to choose one)",
                settings.bootstrap_admin_email,
                password,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await _bootstrap_admin()
    except Exception:  # noqa: BLE001
        # Don't crash the API if bootstrap fails (e.g. migrations not yet run).
        logger.exception("Admin bootstrap failed; continuing")
    yield


app = FastAPI(
    title="Tabula",
    description=DESCRIPTION,
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(TabulaError)
async def _tabula_error(_: Request, exc: TabulaError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


app.include_router(api_router)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {"name": "Tabula", "version": app.version, "docs": "/docs"}
