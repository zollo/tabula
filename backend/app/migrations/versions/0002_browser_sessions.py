"""Browser collection: saved browser sessions, per-platform source and pacing.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scraper_sessions",
        sa.Column("platform", sa.Enum("x", "bluesky", "reddit", name="platform", native_enum=False, length=32), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("storage_state", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("active", "cooling_down", "needs_attention", "disabled", name="session_status", native_enum=False, length=32), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("next_allowed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pages_day", sa.String(length=10), nullable=True),
        sa.Column("pages_today", sa.Integer(), nullable=False),
        sa.Column("pages_total", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Existing rows get the defaults; the model sets them for new rows.
    op.add_column("system_settings", sa.Column("platform_sources", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("system_settings", sa.Column("browser_page_interval_seconds", sa.Integer(), nullable=False, server_default="20"))
    op.add_column("system_settings", sa.Column("browser_daily_page_cap", sa.Integer(), nullable=False, server_default="400"))
    with op.batch_alter_table("system_settings") as batch:
        batch.alter_column("platform_sources", server_default=None)
        batch.alter_column("browser_page_interval_seconds", server_default=None)
        batch.alter_column("browser_daily_page_cap", server_default=None)


def downgrade() -> None:
    op.drop_column("system_settings", "browser_daily_page_cap")
    op.drop_column("system_settings", "browser_page_interval_seconds")
    op.drop_column("system_settings", "platform_sources")
    op.drop_table("scraper_sessions")
