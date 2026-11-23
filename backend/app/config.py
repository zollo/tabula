"""Application settings, loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder signing keys shipped in examples; refused in production.
_PLACEHOLDER_SECRETS = {
    "",
    "change-me",
    "change-me-to-a-long-random-string",
    "CHANGE_ME_openssl_rand_hex_32",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    env: str = Field(default="development", alias="TABULA_ENV")
    secret_key: str = Field(default="change-me", alias="SECRET_KEY")
    cors_origins: str = Field(default="http://localhost:5173", alias="CORS_ORIGINS")
    # Public URL users reach Tabula on; shown on the About page and used in links.
    public_url: str = Field(default="http://localhost:8080", alias="TABULA_PUBLIC_URL")

    # Build metadata (baked into the image at build time)
    app_version: str = Field(default="dev", alias="TABULA_VERSION")
    git_commit: str = Field(default="unknown", alias="TABULA_GIT_COMMIT")
    build_time: str = Field(default="unknown", alias="TABULA_BUILD_TIME")

    # Database
    postgres_user: str = Field(default="tabula", alias="POSTGRES_USER")
    postgres_password: str = Field(default="tabula", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="tabula", alias="POSTGRES_DB")
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    database_url_override: str | None = Field(default=None, alias="DATABASE_URL")
    # Open a fresh connection per session instead of pooling (tests; always on for SQLite).
    db_null_pool: bool = Field(default=False, alias="DB_NULL_POOL")

    # Auth
    access_token_expire_minutes: int = Field(default=1440, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    # Default for the admin-editable "allow sign-up" system setting (the saved
    # setting wins once an admin changes it).
    allow_registration: bool = Field(default=True, alias="ALLOW_REGISTRATION")

    # Bootstrap admin (created on startup if it does not exist). With no
    # password configured, a random one is generated and logged once.
    bootstrap_admin_email: str = Field(default="admin@example.com", alias="BOOTSTRAP_ADMIN_EMAIL")
    bootstrap_admin_password: str = Field(default="", alias="BOOTSTRAP_ADMIN_PASSWORD")

    # Background worker (Celery; Redis is only the broker, no durable data).
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    # How often beat looks for tracked accounts that are due for a scan.
    scheduler_tick_seconds: int = Field(default=60, alias="SCHEDULER_TICK_SECONDS")
    # Pages of history fetched per backfill task run before it re-queues itself.
    backfill_pages_per_run: int = Field(default=5, alias="BACKFILL_PAGES_PER_RUN")
    # Run tasks inline instead of through Redis (tests / single-process dev).
    celery_eager: bool = Field(default=False, alias="CELERY_TASK_ALWAYS_EAGER")

    # Platform credentials. These are defaults: an admin can override each one
    # from System settings (stored encrypted). BlueSky's public AppView needs none.
    x_bearer_token: str = Field(default="", alias="X_BEARER_TOKEN")
    reddit_client_id: str = Field(default="", alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str = Field(default="", alias="REDDIT_CLIENT_SECRET")
    reddit_user_agent: str = Field(default="tabula/1.0 (archiver)", alias="REDDIT_USER_AGENT")
    bluesky_appview_url: str = Field(
        default="https://public.api.bsky.app", alias="BLUESKY_APPVIEW_URL"
    )

    # Google sign-in (OAuth 2.0 / OpenID Connect). Disabled unless both are set.
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    # Must match an "Authorized redirect URI" of the OAuth client; defaults to
    # TABULA_PUBLIC_URL + /auth/google/callback (a route of the web UI).
    google_redirect_uri: str = Field(default="", alias="GOOGLE_REDIRECT_URI")


    @model_validator(mode="after")
    def _require_real_secret(self) -> Settings:
        if self.env == "production" and self.secret_key.strip() in _PLACEHOLDER_SECRETS:
            raise ValueError("SECRET_KEY must be set to a random value in production")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL used by the app."""
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Sync URL used by Alembic migrations."""
        return self.database_url.replace("+asyncpg", "+psycopg2")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id.strip() and self.google_client_secret.strip())

    @property
    def google_callback_url(self) -> str:
        return self.google_redirect_uri or f"{self.public_url.rstrip('/')}/auth/google/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
