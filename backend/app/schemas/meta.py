"""Build info schema."""
from __future__ import annotations

from pydantic import BaseModel


class BuildInfo(BaseModel):
    version: str
    git_commit: str
    build_time: str
    environment: str
    public_url: str
    api_docs_url: str
    openapi_url: str
