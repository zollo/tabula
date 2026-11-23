"""Public (no-login) collection: shared request pacing and its interval.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request_pacers",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hold_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.add_column("system_settings", sa.Column("public_request_interval_seconds", sa.Integer(), nullable=False, server_default="10"))
    op.add_column("social_accounts", sa.Column("scan_cursor", sa.String(length=1024), nullable=True))
    with op.batch_alter_table("system_settings") as batch:
        batch.alter_column("public_request_interval_seconds", server_default=None)


def downgrade() -> None:
    op.drop_column("social_accounts", "scan_cursor")
    op.drop_column("system_settings", "public_request_interval_seconds")
    op.drop_table("request_pacers")
