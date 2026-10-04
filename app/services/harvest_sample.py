"""A harvest day's ABW sample comes from its harvests.

Every harvest write (create, update, delete) replaces the day's sample with the one
its harvests give, so a caller - an AI agent above all - only ever writes harvests:
- ABW: total harvested weight over total harvested count, so a big harvest weighs
  more than a small one;
- time: a minute after the day's last harvest, so the sample reads the pond after
  all of them (population counts every harvest at or before a sample's time);
- no harvests left: no sample.
"""

from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyLog, Harvest

_LAST_MINUTE = dtime(23, 59)


def sample_from_harvests(harvests: list[tuple[dtime, Decimal, Decimal]]) -> tuple[Decimal, dtime] | None:
    """(ABW g, sample time) from a day's (harvest_time, biomass_kg, sampled_abw_g) rows.

    Counts come from each harvest's own ABW rather than its rounded estimated_count, so a
    single harvest gives back exactly its ABW."""
    weighed = [h for h in harvests if h[1] > 0 and h[2] > 0]
    if not weighed:
        return None
    kg = sum((h[1] for h in weighed), Decimal("0"))
    count = sum((h[1] * Decimal("1000") / h[2] for h in weighed), Decimal("0"))
    abw = (kg * Decimal("1000") / count).quantize(Decimal("0.01"))
    last = max(h[0] for h in harvests).replace(second=0, microsecond=0)
    # A minute later stays on the same day; at 23:59 the sample shares the harvest's minute,
    # which still reads the pond after it.
    after = last if last >= _LAST_MINUTE else (datetime.combine(date.min, last) + timedelta(minutes=1)).time()
    return abw, after


async def resync_harvest_sample(db: AsyncSession, daily_log_id: UUID) -> None:
    """Replace the day's ABW sample with the one its harvests give. Call after the harvest change is flushed."""
    rows = (
        await db.execute(
            select(Harvest.harvest_time, Harvest.biomass_kg, Harvest.sampled_abw_g).where(
                Harvest.daily_log_id == daily_log_id
            )
        )
    ).all()
    log = await db.get(DailyLog, daily_log_id)
    if log is None:
        return
    sample = sample_from_harvests([tuple(r) for r in rows])
    log.abw_g, log.abw_sample_time = sample if sample else (None, None)
