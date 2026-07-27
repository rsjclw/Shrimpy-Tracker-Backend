"""Twice-daily weather sweep, at 05:00 and 17:00 in each grid's local time.

Deliberately not "sleep until 05:00". The loop ticks on a short interval and
asks, per grid, whether the most recent scheduled slot has already been synced.
That makes it idempotent, self-healing across restarts and deploys, and correct
when the container happens to be down at the exact minute - none of which a
sleep-until-the-hour design gives you.
"""
import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal
from app.models import Cycle, Grid, Pond
from app.services import weather

logger = logging.getLogger(__name__)

TICK_SECONDS = 15 * 60
# Any positive int; shared by every container so only one sweeps at a time.
ADVISORY_LOCK_KEY = 4_812_055_001


def sync_hours() -> list[int]:
    hours = []
    for part in settings.weather_sync_hours.split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23:
            hours.append(int(part))
    return sorted(set(hours)) or [5, 17]


def _zone(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or settings.default_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def most_recent_slot(now: datetime, tz_name: str | None, hours: list[int]) -> datetime:
    """The latest scheduled instant at or before `now`, as UTC.

    Looks back into yesterday so a container that was down overnight still
    fires on its next tick rather than waiting for tomorrow's slot.
    """
    zone = _zone(tz_name)
    local_now = now.astimezone(zone)

    candidates = [
        datetime.combine(local_now.date() + timedelta(days=offset), time(hour), zone)
        for offset in (0, -1)
        for hour in hours
    ]
    past = [candidate for candidate in candidates if candidate <= local_now]
    return max(past).astimezone(timezone.utc)


def is_due(grid: Grid, now: datetime, hours: list[int]) -> bool:
    if grid.latitude is None or grid.longitude is None:
        return False
    if grid.weather_synced_at is None:
        return True
    synced = grid.weather_synced_at
    if synced.tzinfo is None:
        synced = synced.replace(tzinfo=timezone.utc)
    return synced < most_recent_slot(now, grid.timezone, hours)


async def earliest_needed_date(db: AsyncSession, grid: Grid) -> date | None:
    """Start of the oldest cycle under this grid - how far back weather matters.

    A trend is plotted per cycle, so there is nothing to say about the days
    before the grid's first stocking.
    """
    result = await db.execute(
        select(func.min(Cycle.start_date))
        .join(Pond, Pond.id == Cycle.pond_id)
        .where(Pond.grid_id == grid.id)
    )
    return result.scalar_one_or_none()


async def _acquire_lock(db: AsyncSession) -> bool:
    """Session-scoped advisory lock so multiple containers don't double-fetch."""
    result = await db.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
    )
    return bool(result.scalar())


async def _release_lock(db: AsyncSession) -> None:
    await db.execute(
        text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
    )


async def sweep_once() -> int:
    """Sync every grid whose scheduled slot has passed. Returns grids synced."""
    hours = sync_hours()
    now = datetime.now(timezone.utc)
    synced = 0

    async with SessionLocal() as db:
        if not await _acquire_lock(db):
            logger.debug("Weather sweep skipped: another worker holds the lock")
            return 0
        try:
            result = await db.execute(
                select(Grid).where(
                    Grid.latitude.is_not(None), Grid.longitude.is_not(None)
                )
            )
            for grid in result.scalars().all():
                if not is_due(grid, now, hours):
                    continue
                if await weather.sync_grid(db, grid):
                    synced += 1
                # The forecast call only reaches 7 days back; anything older
                # that is still missing comes from the archive.
                start = await earliest_needed_date(db, grid)
                if start is not None:
                    await weather.backfill_missing(db, grid, start)
            await db.commit()
        finally:
            await _release_lock(db)

    if synced:
        logger.info("Weather sweep synced %d grid(s)", synced)
    return synced


async def run_forever() -> None:
    while True:
        try:
            await sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed sweep must not kill the loop - try again next tick.
            logger.exception("Weather sweep failed")
        await asyncio.sleep(TICK_SECONDS)
