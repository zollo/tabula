"""User accounts: regular users and admins."""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin, UUIDMixin, str_enum

if TYPE_CHECKING:
    from app.models.tracking import Tracking


class UserRole(enum.StrEnum):
    user = "user"
    admin = "admin"


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # None for accounts created through Google sign-in until a password is set.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        str_enum(UserRole, "user_role"), default=UserRole.user, nullable=False
    )
    # False = can't sign in at all.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Self-registered users can sign in and browse, but can't track anyone until
    # an admin enables them. Admin-created accounts start enabled.
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Linked Google account: the stable OpenID `sub`, and its email for display.
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    google_email: Mapped[str | None] = mapped_column(String(320))

    trackings: Mapped[list["Tracking"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="raise"
    )

    @property
    def has_password(self) -> bool:
        return bool(self.hashed_password)

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin

    @property
    def can_track(self) -> bool:
        """Admins can always track; everyone else once an admin enables them."""
        return self.is_admin or self.is_enabled
