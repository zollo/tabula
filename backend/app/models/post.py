"""An archived post (or comment / repost) of a social account."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.db import Base
from app.models.base import JSONType, UUIDMixin, str_enum


class PostKind(enum.StrEnum):
    post = "post"
    reply = "reply"
    repost = "repost"
    quote = "quote"
    comment = "comment"  # Reddit comments


class Post(UUIDMixin, Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("social_account_id", "external_id", name="uq_post"),
        Index("ix_posts_account_posted", "social_account_id", "posted_at"),
    )

    social_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[PostKind] = mapped_column(
        str_enum(PostKind, "post_kind"), default=PostKind.post, nullable=False
    )
    url: Mapped[str | None] = mapped_column(String(1024))
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Image/video URLs as returned by the platform (not downloaded).
    media: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    # Likes, reposts, replies, score... as of the last time the post was fetched.
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    # The platform's full payload, kept so nothing is lost if we parse more later.
    raw: Mapped[dict[str, Any]] = deferred(
        mapped_column(JSONType, nullable=False, default=dict)
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
