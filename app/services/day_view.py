"""Build the full DayView and DaySummary for a cycle/date.

Centralizes the joining of raw rows + metric computation so routers stay thin.
"""
from datetime import date as ddate
from datetime import time as dtime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Cycle, DailyLog, FeedType, FeedingSession, Grid, Harvest, Pond, PopulationSample, WaterParameters
from app.schemas import (
    DayMetrics,
    DaySummary,
    DayView,
    FeedingFeedType,
    FeedingOut,
    HarvestOut,
    SamplingMetrics,
    TreatmentOut,
    WaterParametersOut,
)
from app.services import metrics as M


async def _gather(db: AsyncSession, cycle: Cycle) -> tuple[
    list[M.FeedingRow],
    list[M.SampleRow],
    list[M.AbwRow],
    list[M.HarvestRow],
]:
    """Fetch all rows needed for metric computation in one shot per cycle."""
    feedings_result = await db.execute(
        select(DailyLog.date, FeedingSession.amount_kg, FeedingSession.feed_time)
        .join(FeedingSession, FeedingSession.daily_log_id == DailyLog.id)
        .where(DailyLog.cycle_id == cycle.id)
    )
    feedings = [
        M.FeedingRow(date=r[0], amount_kg=r[1], feed_time=r[2]) for r in feedings_result.all()
    ]

    samples_result = await db.execute(
        select(PopulationSample.date, PopulationSample.population).where(
            PopulationSample.cycle_id == cycle.id
        )
    )
    samples = [M.SampleRow(date=r[0], population=r[1]) for r in samples_result.all()]

    abw_result = await db.execute(
        select(DailyLog.date, DailyLog.abw_g, DailyLog.abw_sample_time).where(
            DailyLog.cycle_id == cycle.id, DailyLog.abw_g.is_not(None)
        )
    )
    abw = [
        M.AbwRow(date=r[0], abw_g=r[1], sample_time=r[2] or dtime(5, 0))
        for r in abw_result.all()
    ]

    harvest_result = await db.execute(
        select(
            DailyLog.date,
            Harvest.harvest_time,
            Harvest.biomass_kg,
            Harvest.estimated_count,
        )
        .join(Harvest, Harvest.daily_log_id == DailyLog.id)
        .where(DailyLog.cycle_id == cycle.id)
    )
    harvests = [
        M.HarvestRow(date=r[0], harvest_time=r[1], biomass_kg=r[2], estimated_count=r[3])
        for r in harvest_result.all()
    ]

    return feedings, samples, abw, harvests


def _compute_metrics(
    cycle: Cycle,
    target: ddate,
    feedings: list[M.FeedingRow],
    samples: list[M.SampleRow],
    abw_history: list[M.AbwRow],
    harvests: list[M.HarvestRow],
) -> DayMetrics:
    daily = M.daily_feed_kg(feedings, target)
    cumulative_start = M.cumulative_feed_before_date(feedings, target)
    cumulative_end = M.cumulative_feed_kg(feedings, target)
    pop = M.estimated_population_end_of_day(cycle.initial_population, samples, harvests, target)
    abw = M.estimated_abw_g(cycle.initial_abw_g, feedings, abw_history, target)
    biomass = M.estimated_biomass_kg(pop, abw)
    harvested = M.harvest_biomass_kg(harvests, target)
    cumulative_harvested = M.cumulative_harvest_biomass_kg(harvests, target)
    fcr_value = M.fcr(cumulative_end, biomass, cumulative_harvested)

    return DayMetrics(
        doc=M.doc_for(cycle.start_date, target),
        daily_feed_kg=daily,
        cumulative_feed_kg=cumulative_start,
        cumulative_feed_start_kg=cumulative_start,
        cumulative_feed_end_kg=cumulative_end,
        abw_g=abw,
        estimated_population=pop,
        estimated_biomass_kg=biomass,
        harvest_biomass_kg=harvested,
        fcr=fcr_value,
    )


def _compute_sampling_metrics(
    target: ddate,
    feedings: list[M.FeedingRow],
    samples: list[M.SampleRow],
    abw_history: list[M.AbwRow],
    harvests: list[M.HarvestRow],
    cycle: Cycle,
) -> SamplingMetrics:
    current = next((a for a in abw_history if a.date == target), None)
    if not current:
        return SamplingMetrics(
            adg_g_per_day=None,
            abw_gain_g=None,
            feed_since_previous_sample_kg=None,
            sample_fcr=None,
        )

    previous_samples = [a for a in abw_history if a.sampled_at < current.sampled_at]
    previous = (
        max(previous_samples, key=lambda a: a.sampled_at)
        if previous_samples
        else M.AbwRow(date=cycle.start_date, abw_g=cycle.initial_abw_g, sample_time=dtime(0, 0))
    )
    previous_pop = M.estimated_population_at(
        cycle.initial_population, samples, harvests, previous.sampled_at
    )
    current_pop = M.estimated_population_at(
        cycle.initial_population, samples, harvests, current.sampled_at
    )
    previous_biomass = M.estimated_biomass_kg(previous_pop, previous.abw_g)
    current_biomass = M.estimated_biomass_kg(current_pop, current.abw_g)
    abw_gain = current.abw_g - previous.abw_g
    period_feed = M.feed_between_samples(feedings, previous, current)
    period_harvested = M.harvest_biomass_between_datetimes(
        harvests, previous.sampled_at, current.sampled_at
    )
    sample_fcr = M.gain_fcr(period_feed, previous_biomass, current_biomass, period_harvested)

    return SamplingMetrics(
        adg_g_per_day=M.adg_g_per_day(previous, current),
        abw_gain_g=abw_gain,
        feed_since_previous_sample_kg=period_feed,
        sample_fcr=sample_fcr,
    )


async def _default_feed_types(db: AsyncSession, cycle: Cycle, target: ddate) -> list[FeedingFeedType]:
    result = await db.execute(
        select(FeedingSession.feed_types)
        .join(DailyLog, FeedingSession.daily_log_id == DailyLog.id)
        .where(DailyLog.cycle_id == cycle.id, DailyLog.date <= target)
        .order_by(DailyLog.date.desc(), FeedingSession.feed_time.desc())
    )
    for feed_types in result.scalars().all():
        if feed_types:
            return [FeedingFeedType.model_validate(f) for f in feed_types]

    farm_result = await db.execute(
        select(Grid.farm_id)
        .join(Pond, Pond.grid_id == Grid.id)
        .where(Pond.id == cycle.pond_id)
    )
    farm_id = farm_result.scalar_one_or_none()
    if not farm_id:
        return []
    feed_type_result = await db.execute(
        select(FeedType)
        .where(FeedType.farm_id == farm_id)
        .order_by(FeedType.created_at, FeedType.brand)
        .limit(1)
    )
    first = feed_type_result.scalar_one_or_none()
    if not first:
        return []
    return [
        FeedingFeedType(
            feed_type_id=str(first.id),
            brand=first.brand,
            type=first.type,
            price_per_kg=first.price_per_kg,
            percentage=Decimal("100"),
            notes=first.notes,
        )
    ]


async def get_day_view(db: AsyncSession, cycle: Cycle, target: ddate) -> DayView:
    """Returns the full day view (or empty shell if no daily_log yet)."""
    result = await db.execute(
        select(DailyLog)
        .where(DailyLog.cycle_id == cycle.id, DailyLog.date == target)
        .options(
            selectinload(DailyLog.feedings),
            selectinload(DailyLog.harvests),
            selectinload(DailyLog.water),
            selectinload(DailyLog.treatments),
        )
    )
    log = result.scalar_one_or_none()

    feedings_all, samples, abw_history, harvests_all = await _gather(db, cycle)
    metrics = _compute_metrics(cycle, target, feedings_all, samples, abw_history, harvests_all)
    sampling = _compute_sampling_metrics(target, feedings_all, samples, abw_history, harvests_all, cycle)
    default_feed_types = await _default_feed_types(db, cycle, target)

    if not log:
        return DayView(
            daily_log_id=None,  # type: ignore[arg-type]
            cycle_id=cycle.id,
            date=target,
            abw_g=None,
            abw_sample_time=None,
            notes=None,
            sampling=sampling,
            default_feed_types=default_feed_types,
            feedings=[],
            harvests=[],
            water=None,
            treatments=[],
            metrics=metrics,
        )

    return DayView(
        daily_log_id=log.id,
        cycle_id=cycle.id,
        date=log.date,
        abw_g=log.abw_g,
        abw_sample_time=log.abw_sample_time,
        notes=log.notes,
        sampling=sampling,
        default_feed_types=default_feed_types,
        feedings=[
            FeedingOut.model_validate(f)
            for f in sorted(log.feedings, key=lambda item: item.feed_time)
        ],
        harvests=[
            HarvestOut.model_validate(h)
            for h in sorted(log.harvests, key=lambda item: item.harvest_time)
        ],
        water=WaterParametersOut.model_validate(log.water) if log.water else None,
        treatments=[TreatmentOut.model_validate(t) for t in log.treatments],
        metrics=metrics,
    )


async def list_day_summaries(
    db: AsyncSession, cycle: Cycle, date_from: ddate, date_to: ddate
) -> list[DaySummary]:
    feedings_all, samples, abw_history, harvests_all = await _gather(db, cycle)

    summaries: list[DaySummary] = []
    current = date_from
    while current <= date_to:
        m = _compute_metrics(cycle, current, feedings_all, samples, abw_history, harvests_all)
        summaries.append(
            DaySummary(
                date=current,
                doc=m.doc,
                daily_feed_kg=m.daily_feed_kg,
                abw_g=m.abw_g,
                estimated_population=m.estimated_population,
                estimated_biomass_kg=m.estimated_biomass_kg,
                harvest_biomass_kg=m.harvest_biomass_kg,
                fcr=m.fcr,
            )
        )
        current = ddate.fromordinal(current.toordinal() + 1)
    return summaries


METRIC_EXTRACTORS = {
    "daily_feed_kg": lambda m: m.daily_feed_kg,
    "cumulative_feed_kg": lambda m: m.cumulative_feed_kg,
    "cumulative_feed_start_kg": lambda m: m.cumulative_feed_start_kg,
    "cumulative_feed_end_kg": lambda m: m.cumulative_feed_end_kg,
    "abw_g": lambda m: m.abw_g,
    "estimated_population": lambda m: Decimal(m.estimated_population) if m.estimated_population is not None else None,
    "estimated_biomass_kg": lambda m: m.estimated_biomass_kg,
    "harvest_biomass_kg": lambda m: m.harvest_biomass_kg,
    "fcr": lambda m: m.fcr,
}

WATER_METRICS = {
    "do_am": WaterParameters.do_am,
    "do_pm": WaterParameters.do_pm,
    "ph_am": WaterParameters.ph_am,
    "ph_pm": WaterParameters.ph_pm,
    "salinity": WaterParameters.salinity,
    "tan": WaterParameters.tan,
    "nitrite": WaterParameters.nitrite,
    "phosphate": WaterParameters.phosphate,
    "calcium": WaterParameters.calcium,
    "magnesium": WaterParameters.magnesium,
    "alkalinity": WaterParameters.alkalinity,
}


async def get_trend(
    db: AsyncSession,
    cycle: Cycle,
    metric: str,
    date_from: ddate,
    date_to: ddate,
) -> list[tuple[ddate, Decimal | None]]:
    if metric not in METRIC_EXTRACTORS and metric not in WATER_METRICS and metric != "sample_fcr":
        raise ValueError(f"Unknown metric: {metric}")

    points: list[tuple[ddate, Decimal | None]] = []

    if metric in WATER_METRICS:
        result = await db.execute(
            select(DailyLog.date, WATER_METRICS[metric])
            .join(WaterParameters, WaterParameters.daily_log_id == DailyLog.id)
            .where(
                DailyLog.cycle_id == cycle.id,
                DailyLog.date >= date_from,
                DailyLog.date <= date_to,
            )
        )
        values = {r[0]: r[1] for r in result.all()}
        current = date_from
        while current <= date_to:
            points.append((current, values.get(current)))
            current = ddate.fromordinal(current.toordinal() + 1)
        return points

    feedings_all, samples, abw_history, harvests_all = await _gather(db, cycle)

    if metric == "sample_fcr":
        current = date_from
        while current <= date_to:
            sampling = _compute_sampling_metrics(
                current, feedings_all, samples, abw_history, harvests_all, cycle
            )
            points.append((current, sampling.sample_fcr))
            current = ddate.fromordinal(current.toordinal() + 1)
        return points

    extractor = METRIC_EXTRACTORS[metric]

    current = date_from
    while current <= date_to:
        m = _compute_metrics(cycle, current, feedings_all, samples, abw_history, harvests_all)
        points.append((current, extractor(m)))
        current = ddate.fromordinal(current.toordinal() + 1)
    return points


async def get_sampling_dates(
    db: AsyncSession,
    cycle: Cycle,
    date_from: ddate,
    date_to: ddate,
) -> set[ddate]:
    result = await db.execute(
        select(DailyLog.date).where(
            DailyLog.cycle_id == cycle.id,
            DailyLog.date >= date_from,
            DailyLog.date <= date_to,
            DailyLog.abw_g.is_not(None),
        )
    )
    return set(result.scalars().all())
