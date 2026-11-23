"""Request pacing shared by every worker, kept in the database.

Unauthenticated endpoints (Reddit's public .json pages) are rate-limited per IP,
and all of Tabula's workers usually share one IP, so the pace has to be global.
Each request claims the next free slot with a conditional UPDATE (the same
pattern the scheduler uses) and sleeps until it. A slot further away than
`max_wait` isn't claimed: the caller gets `Throttled` and tries again later
instead of holding a worker.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.request_pacer import RequestPacer
from app.platforms.base import PlatformError, Throttled

# Longest a request waits for its slot before giving up for now.
MAX_WAIT = timedelta(seconds=60)
# Contention retries before giving up (each one means another worker won).
_ATTEMPTS = 20


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes.
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


class Pacer:
    def __init__(
        self,
        db: AsyncSession,
        key: str,
        interval_seconds: float,
        *,
        max_wait: timedelta = MAX_WAIT,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        label: str = "",
    ):
        self.db = db
        self.key = key
        self.interval = timedelta(seconds=interval_seconds)
        self.max_wait = max_wait
        self._sleep = sleep
        self.label = label or key

    async def _seen(self) -> tuple[bool, datetime | None]:
        row = (
            await self.db.execute(select(RequestPacer.next_at).where(RequestPacer.key == self.key))
        ).first()
        return (row is not None, row[0] if row else None)

    async def wait(self) -> None:
        """Claim the next request slot and sleep until it starts."""
        for _ in range(_ATTEMPTS):
            exists, seen = await self._seen()
            now = datetime.now(UTC)
            slot = max(now, _aware(seen) or now)
            if slot - now > self.max_wait:
                await self.db.commit()
                raise Throttled(f"{self.label} rate limit: waiting until {slot:%H:%M:%S} UTC", slot)
            if not exists:
                try:
                    async with self.db.begin_nested():
                        self.db.add(RequestPacer(key=self.key, next_at=slot + self.interval))
                    claimed = True
                except IntegrityError:
                    claimed = False  # another worker created it first
            else:
                cond = (
                    RequestPacer.next_at.is_(None) if seen is None else RequestPacer.next_at == seen
                )
                result = await self.db.execute(
                    update(RequestPacer)
                    .where(RequestPacer.key == self.key, cond)
                    .values(next_at=slot + self.interval)
                )
                claimed = bool(result.rowcount)
            # Commit so other workers see the claim (this also keeps any posts
            # the caller stored so far, as browser leases do).
            await self.db.commit()
            if claimed:
                delay = (slot - datetime.now(UTC)).total_seconds()
                if delay > 0:
                    await self._sleep(delay)
                # Another worker may have been told to back off while we slept:
                # then this slot is void and we queue again behind the hold.
                hold = (
                    await self.db.execute(
                        select(RequestPacer.hold_until).where(RequestPacer.key == self.key)
                    )
                ).scalar_one_or_none()
                if hold is None or _aware(hold) <= datetime.now(UTC):
                    return
        raise PlatformError(f"{self.label}: too many workers waiting to send", retryable=True)

    async def hold(self, seconds: float) -> datetime:
        """The site said slow down: nobody sends before `now + seconds`."""
        until = datetime.now(UTC) + timedelta(seconds=seconds)
        exists, seen = await self._seen()
        if not exists:
            try:
                async with self.db.begin_nested():
                    self.db.add(RequestPacer(key=self.key, next_at=until, hold_until=until))
            except IntegrityError:
                exists = True
        if exists:
            for column in (RequestPacer.next_at, RequestPacer.hold_until):
                await self.db.execute(
                    update(RequestPacer)
                    .where(RequestPacer.key == self.key, column.is_(None) | (column < until))
                    .values({column.key: until})
                )
        await self.db.commit()
        return until
