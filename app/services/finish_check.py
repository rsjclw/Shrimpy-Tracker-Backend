"""What finishing a cycle on a given day will give, and what would make it wrong.

The end of a cycle is order-sensitive: the pond counts as empty after the last
harvest on its end day (see metrics.pond_empty_at), so the end day should be the
final harvest day, every harvest of that day should be logged before the final ABW
is taken from them, and planned feeds after the final harvest must go - they would
count toward the cycle FCR. This works that out from the cycle's rows so the Finish
dialog can say so before anything is saved.
"""

from dataclasses import dataclass, field
from datetime import date as ddate, datetime, time as dtime
from decimal import Decimal

from app.services import metrics as M


@dataclass(frozen=True)
class LateFeed:
    date: ddate
    feed_time: dtime
    amount_kg: Decimal


@dataclass
class FinishCheck:
    end_date: ddate
    stocked: int
    harvested_count: int
    harvested_kg: Decimal
    survival_rate_pct: Decimal | None
    feed_kg: Decimal
    cycle_fcr: Decimal | None
    # The cycle's last harvest, any day: usually the right end day.
    last_harvest_date: ddate | None
    last_harvest_time: dtime | None
    # Harvests after the end day: the cycle cannot end before them.
    harvests_after_end: int
    # Feeds on the end day after its final harvest: given to an empty pond, so probably never given.
    feeds_after_final_harvest: list[LateFeed] = field(default_factory=list)
    # The end day's ABW sample when it was taken before that day's final harvest.
    sample_before_final_harvest: dtime | None = None


def finish_check(
    end_date: ddate,
    stocked: int,
    feedings: list[M.FeedingRow],
    harvests: list[M.HarvestRow],
    abw_history: list[M.AbwRow],
    today: ddate,
) -> FinishCheck:
    harvested_kg = sum((h.biomass_kg for h in harvests if h.date <= end_date), Decimal("0"))
    feed_kg = M.cumulative_feed_kg(feedings, end_date)
    # Harvests dated after today are predictions or plans: they neither point to the last day
    # nor stop the cycle from ending before them.
    happened = [h for h in harvests if h.date <= today]
    last = max(happened, key=lambda h: h.harvested_at, default=None)
    empty_at = M.pond_empty_at(end_date, harvests)
    final_harvests_on_end = [h for h in harvests if h.date == end_date]

    late = [
        LateFeed(date=f.date, feed_time=f.feed_time, amount_kg=f.amount_kg)
        for f in feedings
        if final_harvests_on_end and f.date == end_date and datetime.combine(f.date, f.feed_time) > empty_at
    ]
    end_sample = next((a for a in abw_history if a.date == end_date), None)
    early_sample = (
        end_sample.sample_time
        if end_sample is not None and final_harvests_on_end and end_sample.sampled_at < empty_at
        else None
    )
    return FinishCheck(
        end_date=end_date,
        stocked=stocked,
        harvested_count=M.harvested_count(harvests, end_date),
        harvested_kg=harvested_kg,
        survival_rate_pct=M.survival_rate_pct(stocked, harvests, end_date),
        feed_kg=feed_kg,
        cycle_fcr=(feed_kg / harvested_kg).quantize(Decimal("0.01")) if harvested_kg > 0 else None,
        last_harvest_date=last.date if last else None,
        last_harvest_time=last.harvest_time if last else None,
        harvests_after_end=sum(1 for h in happened if h.date > end_date),
        feeds_after_final_harvest=sorted(late, key=lambda f: f.feed_time),
        sample_before_final_harvest=early_sample,
    )
