"""Shared plumbing for browser clients: leasing a login, pacing, outcomes.

A browser client implements the same `resolve` / `fetch_page` contract as the
API clients, so the scanner doesn't know the difference. It leases a saved login
from the broker on first use, waits `interval_seconds` between page loads, and
on `aclose()` shuts the browser and releases the login with the outcome.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.models.platform import Platform
from app.platforms.base import PlatformError
from app.platforms.browser.driver import BrowserDriver, Captured, PlaywrightDriver


class Broker(Protocol):
    """Hands out saved logins (see services.scraper_session_service.Broker)."""

    interval_seconds: int

    async def acquire(self) -> dict[str, Any] | None: ...

    async def page_loaded(self) -> None: ...

    async def release(self, outcome: str, error: str | None) -> None: ...


class BrowserClient:
    platform: Platform
    label = "the site"

    def __init__(
        self,
        broker: Broker,
        driver_factory: Callable[[], BrowserDriver] = PlaywrightDriver,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.broker = broker
        self._driver_factory = driver_factory
        self._driver: BrowserDriver | None = None
        self._sleep = sleep
        self._clock = clock
        self._last_load: float | None = None
        self.outcome = "ok"
        self.error: str | None = None

    async def _load(self, fn: Callable[[BrowserDriver], Awaitable[Captured]]) -> Captured:
        if self._driver is None:
            state = await self.broker.acquire()
            driver = self._driver_factory()
            try:
                await driver.start(state)
            except Exception as exc:
                await driver.close()
                await self.broker.release("error", f"Browser failed to start: {exc}")
                raise PlatformError(f"Browser failed to start: {exc}", retryable=True) from exc
            self._driver = driver
        if self._last_load is not None:
            wait = self._last_load + self.broker.interval_seconds - self._clock()
            if wait > 0:
                await self._sleep(wait)
        try:
            captured = await fn(self._driver)
        except PlatformError:
            raise
        except Exception as exc:  # noqa: BLE001  (timeouts, crashed page, ...)
            raise self.fail("error", f"{self.label} page failed to load: {exc}") from exc
        finally:
            self._last_load = self._clock()
        await self.broker.page_loaded()
        return captured

    def fail(self, outcome: str, message: str) -> PlatformError:
        """Remember the outcome for release and build the error to raise."""
        if outcome != "error" or self.outcome == "ok":
            self.outcome = outcome
        self.error = message
        return PlatformError(message, retryable=True)

    async def aclose(self) -> None:
        if self._driver is None:
            return
        try:
            await self._driver.close()
        finally:
            self._driver = None
            await self.broker.release(self.outcome, self.error)
