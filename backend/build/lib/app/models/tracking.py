"""A user's subscription to a social account, with their chosen scan frequency."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.social_account import SocialAccount
    from app.models.user import User


class Tracking(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "trackings"
    __table_args__ = (UniqueConstraint("user_id", "social_account_id", name="uq_tracking"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scan_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship(back_populates="trackings", lazy="raise")
    social_account: Mapped["SocialAccount"] = relationship(
        back_populates="trackings", lazy="selectin"
    )
