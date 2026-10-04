"""Blind feeding: the fixed feed plan a template lays down from stocking day.

The plan is stored as ordinary feeding rows, plus one ABW sample at the cycle's
target the day after the template ends, so FCR, cumulative feed and predictions
read it like any other feeding. These helpers work the plan out; the cycles
router writes it.
"""

from dataclasses import dataclass
from datetime import date as ddate, time as dtime, timedelta
from decimal import Decimal

from app.services.feeding_amounts import round_feed_amount_kg
from app.services.feeding_schedule import feeding_sessions_for

# The target ABW is stamped as a dawn sample, before the day's first feed.
TARGET_SAMPLE_TIME = dtime(5, 0)
# Every feeding a template writes carries this note ("Blind feeding: <template> DOC n"),
# which is how a recalculation finds them again.
BLIND_NOTE = "Blind feeding: "


@dataclass(frozen=True)
class BlindFeedingDay:
    date: ddate
    doc: int
    sessions: list[tuple[dtime, Decimal]]  # (feed time, kg)


@dataclass(frozen=True)
class BlindFeedingPlan:
    days: list[BlindFeedingDay]
    target_sample: tuple[ddate, Decimal] | None  # (date, ABW g)


def daily_amount_kg(rate_per_100k: float, population: int) -> Decimal:
    return round_feed_amount_kg(Decimal(str(rate_per_100k)) * Decimal(population) / Decimal("100000"))


def plan_blind_feeding(
    start_date: ddate,
    population: int,
    daily_feed_per_100k: list[float],
    anchor_feed_time: dtime,
    target_abw_g: Decimal | None,
) -> BlindFeedingPlan:
    sessions = feeding_sessions_for(anchor_feed_time)
    days = []
    for index, rate in enumerate(daily_feed_per_100k):
        total = daily_amount_kg(rate, population)
        days.append(
            BlindFeedingDay(
                date=start_date + timedelta(days=index),
                doc=index + 1,
                sessions=[(feed_time, round_feed_amount_kg(total * fraction)) for feed_time, fraction in sessions],
            )
        )
    target_sample = None
    if target_abw_g is not None:
        target_sample = (start_date + timedelta(days=len(daily_feed_per_100k)), target_abw_g)
    return BlindFeedingPlan(days=days, target_sample=target_sample)


def blind_feeding_window(start_date: ddate, day_count: int) -> tuple[ddate, ddate] | None:
    """First and last day a template of `day_count` days feeds; None when there is no template."""
    if day_count <= 0:
        return None
    return start_date, start_date + timedelta(days=day_count - 1)
