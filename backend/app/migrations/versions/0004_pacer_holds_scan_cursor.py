"""Pacer holds that void reserved slots, and resumable deferred scans.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_pacers", sa.Column("hold_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("social_accounts", sa.Column("scan_cursor", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("social_accounts", "scan_cursor")
    op.drop_column("request_pacers", "hold_until")
