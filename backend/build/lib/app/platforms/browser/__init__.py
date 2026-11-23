"""Collecting posts with a headless browser and a saved login (X, Reddit)."""
from __future__ import annotations

from app.platforms.browser.base import Broker, BrowserClient
from app.platforms.browser.driver import BrowserDriver, Captured, PlaywrightDriver
from app.platforms.browser.reddit import RedditBrowserClient
from app.platforms.browser.x import XBrowserClient

__all__ = [
    "Broker",
    "BrowserClient",
    "BrowserDriver",
    "Captured",
    "PlaywrightDriver",
    "RedditBrowserClient",
    "XBrowserClient",
]
