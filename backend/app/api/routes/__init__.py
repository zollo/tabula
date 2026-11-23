"""Aggregate API router, mounted at /api."""
from fastapi import APIRouter

from app.api.routes import auth, health, system, trackings, users

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(system.router)
api_router.include_router(trackings.router)
