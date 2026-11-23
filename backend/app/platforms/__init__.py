"""Platform clients. `get_client` builds one with the credentials in effect."""
from __future__ import annotations

from app.models.platform import Platform
from app.platforms.base import (
    FetchedPost,
    Page,
    PlatformClient,
    PlatformError,
    PlatformNotConfigured,
    ProfileInfo,
    ProfileNotFound,
)
from app.platforms.bluesky import BlueskyClient
from app.platforms.reddit import RedditClient
from app.platforms.x import XClient


def get_client(platform: Platform, creds: dict[str, str]) -> PlatformClient:
    """`creds` comes from settings_service.platform_credentials()."""
    if platform == Platform.bluesky:
        return BlueskyClient(creds["bluesky_appview_url"])
    if platform == Platform.reddit:
        return RedditClient(
            creds["reddit_client_id"], creds["reddit_client_secret"], creds["reddit_user_agent"]
        )
    if platform == Platform.x:
        return XClient(creds["x_bearer_token"])
    raise PlatformNotConfigured(f"Unsupported platform {platform}")


def get_browser_client(platform: Platform, broker):
    """A browser client for `platform`, leasing logins from `broker`.

    Built in the browser worker only (it needs Playwright and Chromium).
    """
    from app.platforms.browser import RedditBrowserClient, XBrowserClient

    if platform == Platform.x:
        return XBrowserClient(broker)
    if platform == Platform.reddit:
        return RedditBrowserClient(broker)
    raise PlatformNotConfigured(f"{platform} can't be collected with a browser")


__all__ = [
    "FetchedPost",
    "Page",
    "PlatformClient",
    "PlatformError",
    "PlatformNotConfigured",
    "ProfileInfo",
    "ProfileNotFound",
    "get_browser_client",
    "get_client",
]
