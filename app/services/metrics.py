"""Pure functions for computing derived metrics.

All inputs are simple data; no DB access. The router layer queries
the data and passes it in. This keeps these functions easy to unit-test.
"""
from dataclasses import dataclass
from datetime import date as ddate
from datetime import datetime, time as dtime
from decimal import Decimal


def doc_for(start_date: ddate, target: ddate) -> int:
    """Day Of Culture. DOC 1 = stocking day."""
    return (target - start_date).days + 1


@dataclass(frozen=True)
class FeedingRow:
    date: ddate
    amount_kg: Decimal
    feed_time: dtime = dtime(0, 0)


@dataclass(frozen=True)
class SampleRow:
    date: ddate
    population: int


@dataclass(frozen=True)
class AbwRow:
    date: ddate
    abw_g: Decimal
    sample_time: dtime = dtime(5, 0)

    @property
    def sampled_at(self) -> datetime:
        return datetime.combine(self.date, self.sample_time)


@dataclass(frozen=True)
class HarvestRow:
    date: ddate
    harvest_time: dtime
    biomass_kg: Decimal
    estimated_count: int

    @property
    def harvested_at(self) -> datetime:
        return datetime.combine(self.date, self.harvest_time)


def daily_feed_kg(feedings: list[FeedingRow], target: ddate) -> Decimal:
    return sum((f.amount_kg for f in feedings if f.date == target), Decimal("0"))


def cumulative_feed_kg(feedings: list[FeedingRow], up_to: ddate) -> Decimal:
    return sum((f.amount_kg for f in feedings if f.date <= up_to), Decimal("0"))


def cumulative_feed_before_date(feedings: list[FeedingRow], target: ddate) -> Decimal:
    """Feed already given before the target date begins."""
    target_start = datetime.combine(target, dtime(0, 0))
    return sum((f.amount_kg for f in feedings if _feeding_at(f) < target_start), Decimal("0"))


def estimated_population(
    initial_population: int, samples: list[SampleRow], up_to: ddate
) -> int:
    """Last-known-value carry-forward from population_samples."""
    candidates = [s for s in samples if s.date <= up_to]
    if not candidates:
        return initial_population
    return max(candidates, key=lambda s: s.date).population


def cycle_end_date(cycle, today: ddate) -> ddate | None:
    """Last day of a cycle that has ended, or None while it runs. A reopened cycle keeps its end."""
    if cycle.status == "active":
        return cycle.actual_end_date

    # Older completed cycles may predate actual_end_date being maintained.
    # Their planned end is the best available boundary. Never let a closed
    # cycle extend into the future, even if that planned date is still ahead.
    end_date = cycle.actual_end_date or cycle.planned_end_date or today
    return min(end_date, today)


def pond_empty_at(end_date: ddate | None, harvests: list[HarvestRow]) -> datetime | None:
    """When an ended cycle's pond is empty: at its last harvest on the end day, or at the end
    of that day when nothing was harvested then. Whatever the model still counts after that
    died along the way. The end day, not the cycle's last harvest: a crashed cycle with an
    early partial harvest still had shrimp until it ended."""
    if end_date is None:
        return None
    final_harvests = [h.harvested_at for h in harvests if h.date == end_date]
    return max(final_harvests) if final_harvests else datetime.combine(end_date, dtime.max)


def harvested_count(harvests: list[HarvestRow], up_to: ddate) -> int:
    return sum(h.estimated_count for h in harvests if h.date <= up_to)


def survival_rate_pct(initial_population: int, harvests: list[HarvestRow], up_to: ddate) -> Decimal | None:
    """Share of the stocked shrimp that were harvested: the cycle's survival rate once it has ended."""
    if initial_population <= 0:
        return None
    pct = Decimal(harvested_count(harvests, up_to)) * Decimal("100") / Decimal(initial_population)
    return pct.quantize(Decimal("0.1"))


def estimated_population_at(
    initial_population: int,
    samples: list[SampleRow],
    harvests: list[HarvestRow],
    at: datetime,
    empty_at: datetime | None = None,
) -> int:
    """Population at a timestamp using date-level samples as start-of-day anchors;
    0 from `empty_at` (see pond_empty_at) on."""
    if empty_at is not None and at >= empty_at:
        return 0
    candidates = [s for s in samples if s.date <= at.date()]
    if candidates:
        anchor = max(candidates, key=lambda s: s.date)
        base_population = anchor.population
        anchor_at = datetime.combine(anchor.date, dtime(0, 0))
    else:
        base_population = initial_population
        anchor_at = datetime.min

    harvested_count = sum(
        h.estimated_count for h in harvests if anchor_at < h.harvested_at <= at
    )
    return max(base_population - harvested_count, 0)


def estimated_population_end_of_day(
    initial_population: int,
    samples: list[SampleRow],
    harvests: list[HarvestRow],
    up_to: ddate,
    empty_at: datetime | None = None,
) -> int:
    return estimated_population_at(
        initial_population, samples, harvests, datetime.combine(up_to, dtime.max), empty_at
    )


def carry_forward_abw(
    initial_abw_g: Decimal, abw_history: list[AbwRow], up_to: ddate
) -> Decimal:
    candidates = [a for a in abw_history if a.date <= up_to and a.abw_g is not None]
    if not candidates:
        return initial_abw_g
    return max(candidates, key=lambda a: a.date).abw_g


def _feeding_at(feeding: FeedingRow) -> datetime:
    return datetime.combine(feeding.date, feeding.feed_time)


def feed_between_datetimes(
    feedings: list[FeedingRow], start_at: datetime, end_at: datetime
) -> Decimal:
    return sum(
        (f.amount_kg for f in feedings if start_at < _feeding_at(f) <= end_at),
        Decimal("0"),
    )


def feed_between_datetimes_before_end(
    feedings: list[FeedingRow], start_at: datetime, end_at: datetime
) -> Decimal:
    return sum(
        (f.amount_kg for f in feedings if start_at < _feeding_at(f) < end_at),
        Decimal("0"),
    )


def estimated_abw_g(
    initial_abw_g: Decimal,
    feedings: list[FeedingRow],
    abw_history: list[AbwRow],
    up_to: ddate,
) -> Decimal:
    samples = sorted(abw_history, key=lambda a: a.sampled_at)
    if not samples:
        return initial_abw_g

    same_day_samples = [a for a in samples if a.date == up_to]
    if same_day_samples:
        return max(same_day_samples, key=lambda a: a.sampled_at).abw_g

    target_at = datetime.combine(up_to, dtime(0, 0))
    previous_samples = [a for a in samples if a.sampled_at <= target_at]
    if not previous_samples:
        return initial_abw_g

    previous = previous_samples[-1]
    next_samples = [a for a in samples if a.sampled_at > target_at]
    if not next_samples:
        return previous.abw_g

    current = next_samples[0]
    total_feed = feed_between_datetimes(feedings, previous.sampled_at, current.sampled_at)
    if total_feed <= 0:
        total_seconds = Decimal(str((current.sampled_at - previous.sampled_at).total_seconds()))
        elapsed_seconds = Decimal(str((target_at - previous.sampled_at).total_seconds()))
        ratio = Decimal("0") if total_seconds <= 0 else elapsed_seconds / total_seconds
    else:
        feed_to_target = feed_between_datetimes_before_end(feedings, previous.sampled_at, target_at)
        ratio = feed_to_target / total_feed

    ratio = min(max(ratio, Decimal("0")), Decimal("1"))
    estimated = previous.abw_g + ((current.abw_g - previous.abw_g) * ratio)
    return estimated.quantize(Decimal("0.0001"))


def estimated_biomass_kg(population: int, abw_g: Decimal) -> Decimal:
    return (Decimal(population) * abw_g) / Decimal("1000")


def harvest_biomass_kg(harvests: list[HarvestRow], target: ddate) -> Decimal:
    return sum((h.biomass_kg for h in harvests if h.date == target), Decimal("0"))


def cumulative_harvest_biomass_kg(harvests: list[HarvestRow], up_to: ddate) -> Decimal:
    return sum((h.biomass_kg for h in harvests if h.date <= up_to), Decimal("0"))


def harvest_biomass_between_datetimes(
    harvests: list[HarvestRow], start_at: datetime, end_at: datetime
) -> Decimal:
    return sum(
        (h.biomass_kg for h in harvests if start_at < h.harvested_at <= end_at),
        Decimal("0"),
    )


def fcr(
    cumulative_feed_kg_value: Decimal,
    current_biomass_kg: Decimal,
    cumulative_harvest_biomass_kg_value: Decimal = Decimal("0"),
) -> Decimal | None:
    """Estimated FCR = total feed / standing plus harvested biomass."""
    production_biomass = current_biomass_kg + cumulative_harvest_biomass_kg_value
    if production_biomass <= 0:
        return None
    return (cumulative_feed_kg_value / production_biomass).quantize(Decimal("0.01"))


def gain_fcr(
    feed_kg: Decimal,
    previous_biomass_kg: Decimal,
    current_biomass_kg: Decimal,
    harvested_biomass_kg: Decimal = Decimal("0"),
) -> Decimal | None:
    """Sample-period FCR = feed / produced biomass. None if production did not increase."""
    gained = current_biomass_kg + harvested_biomass_kg - previous_biomass_kg
    if gained <= 0:
        return None
    return (feed_kg / gained).quantize(Decimal("0.01"))


def feed_between_samples(
    feedings: list[FeedingRow], previous: AbwRow, current: AbwRow
) -> Decimal:
    return feed_between_datetimes(feedings, previous.sampled_at, current.sampled_at)


def adg_g_per_day(previous: AbwRow, current: AbwRow) -> Decimal | None:
    days = Decimal(str((current.sampled_at - previous.sampled_at).total_seconds())) / Decimal("86400")
    if days <= 0:
        return None
    return ((current.abw_g - previous.abw_g) / days).quantize(Decimal("0.001"))
