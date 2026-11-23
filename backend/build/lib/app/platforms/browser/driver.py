"""The browser itself: a thin Playwright wrapper the browser clients drive.

Clients never parse HTML. They open a page and collect the JSON the site's own
web app loads (X's GraphQL, Reddit's `.json` listings). `BrowserDriver` is the
seam tests replace with a fake; `PlaywrightDriver` is the real Chromium.

Playwright is only installed in the browser-worker image (`pip install
".[browser]"`), so it's imported lazily.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("tabula.browser")


@dataclass
class Captured:
    """What a page load produced."""

    final_url: str
    status: int | None = None  # status of the document itself
    bodies: list[Any] = field(default_factory=list)  # matching JSON responses
    statuses: list[int] = field(default_factory=list)  # statuses of matching responses


class BrowserDriver(Protocol):
    async def start(self, storage_state: dict[str, Any] | None) -> None: ...

    async def collect(
        self,
        url: str,
        match: str,
        *,
        enough: Callable[[list[Any]], bool],
        max_scrolls: int,
    ) -> Captured: ...

    async def get_json(self, url: str) -> Captured: ...

    async def close(self) -> None: ...


class PlaywrightDriver:
    """Headless Chromium with one isolated context (one saved login)."""

    def __init__(self, *, scroll_pause: float = 2.0, nav_timeout_ms: int = 45_000):
        self.scroll_pause = scroll_pause
        self.nav_timeout_ms = nav_timeout_ms
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    async def start(self, storage_state: dict[str, Any] | None) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        # Containers have a tiny /dev/shm; Chromium crashes without this flag.
        launch: dict[str, Any] = {"headless": True, "args": ["--disable-dev-shm-usage"]}
        if path := os.environ.get("TABULA_CHROMIUM_PATH"):
            launch["executable_path"] = path
        self._browser = await self._pw.chromium.launch(**launch)
        self._context = await self._browser.new_context(
            storage_state=storage_state,
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        # Skip images, media and fonts: we only want the data responses.
        await self._context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )
        self._page = await self._context.new_page()

    async def collect(self, url, match, *, enough, max_scrolls) -> Captured:
        page = self._page
        captured = Captured(final_url=url)
        pending: list[asyncio.Task] = []

        async def read(resp) -> None:
            captured.statuses.append(resp.status)
            if resp.ok:
                try:
                    captured.bodies.append(await resp.json())
                except Exception:  # noqa: BLE001  (not JSON, or the page navigated away)
                    pass

        def on_response(resp) -> None:
            if match in resp.url:
                pending.append(asyncio.ensure_future(read(resp)))

        page.on("response", on_response)
        try:
            doc = await page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            captured.status = doc.status if doc else None
            idle = 0
            for _ in range(max_scrolls + 1):
                before = len(captured.bodies)
                await asyncio.sleep(self.scroll_pause)
                await asyncio.gather(*pending, return_exceptions=True)
                if enough(captured.bodies) or 429 in captured.statuses:
                    break
                idle = idle + 1 if len(captured.bodies) == before else 0
                if idle >= 2:
                    break  # nothing more is loading
                await page.mouse.wheel(0, 6000)
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            page.remove_listener("response", on_response)
        captured.final_url = page.url
        return captured

    async def get_json(self, url: str) -> Captured:
        resp = await self._page.goto(url, wait_until="load", timeout=self.nav_timeout_ms)
        captured = Captured(final_url=self._page.url, status=resp.status if resp else None)
        if resp is not None:
            try:
                captured.bodies.append(json.loads(await resp.text()))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        return captured

    async def close(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:  # noqa: BLE001
                    logger.debug("Error closing the browser", exc_info=True)
        if self._pw is not None:
            await self._pw.stop()
