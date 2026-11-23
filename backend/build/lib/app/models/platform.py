"""Supported social media platforms."""
from __future__ import annotations

import enum


class Platform(enum.StrEnum):
    x = "x"
    bluesky = "bluesky"
    reddit = "reddit"


PLATFORM_LABELS: dict[Platform, str] = {
    Platform.x: "X (Twitter)",
    Platform.bluesky: "Bluesky",
    Platform.reddit: "Reddit",
}
