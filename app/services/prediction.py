import asyncio
import dataclasses
from datetime import date as ddate
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Cycle, DailyLog, FeedType, FeedingSession, Grid, Harvest, Pond, PopulationSample, Treatment, WaterParameters
from app.schemas.feeding import FeedingFeedType
from app.schemas.prediction import (
    PredictionDailyRowOut,
    PredictionFeedingOut,
    PredictionGeneratedCounts,
    PredictionPartialHarvestOut,
    PredictionResultOut,
    PredictionSummaryOut,
)
from app.services import prediction_core as core
from app.services.day_view import get_prediction_baseline
from app.services.feeding_amounts import round_feed_amount_kg

# Prediction simulation runs on the vendored source-of-truth algorithm in
# prediction_core (kept byte-identical to simulation/predict.py). This module
# only adapts DB rows -> core.Config and core results -> API schemas.
HARVEST_TIME = dtime(5, 0)
FEEDING_SPLIT = (
    (dtime(6, 0), Decimal("0.25")),
    (dtime(10, 0), Decimal("0.30")),
    (dtime(14, 0), Decimal("0.30")),
    (dtime(18, 0), Decimal("0.15")),
)

DEFAULT_CONFIG = {
    "cycle": {"preparation_day": 20, "maximum_shrimp_size_g": 100},
    "growth": {
        "target_fcr": 1.3,
        "maximum_adg_g_per_day": 0.5,
        "initial_feeding_index": 0.55,
        "feeding_index_increment": 0.01,
        "maximum_feeding_index": 0.7,
    },
    "capacity": {
        "stable_carrying_capacity_kg_per_m2": 2,
        "final_carrying_capacity_kg_per_m2": 3,
    },
    "harvest": {
        "minimum_partial_harvest_biomass_kg": 350,
        "harvest_fixed_cost_per_event": 500000,
    },
    "prices": {
        "harvest_price_points": [
            {"count_size": 200, "price_per_kg": 20000},
            {"count_size": 100, "price_per_kg": 52000},
            {"count_size": 90, "price_per_kg": 53000},
            {"count_size": 80, "price_per_kg": 55000},
            {"count_size": 70, "price_per_kg": 57000},
            {"count_size": 60, "price_per_kg": 60000},
            {"count_size": 50, "price_per_kg": 64000},
            {"count_size": 40, "price_per_kg": 70000},
            {"count_size": 30, "price_per_kg": 75000},
            {"count_size": 20, "price_per_kg": 82000},
        ],
    },
    "costs": {
        "pl_price_per_piece": 54,
        "electricity_kwh": 6,
        "electricity_price_per_kwh": 1590,
        "labor_cost_per_day": 100000,
        "probiotics_cost_per_day": 42000,
        "disinfection_cost_per_day": 70000,
        "liming_cost_per_day": 30000,
    },
}


class PredictionError(ValueError):
    pass


@dataclasses.dataclass
class FeedPlanRow:
    feed_type_id: str
    name: str
    brand: str
    type: str
    price_per_kg: float
    maximum_daily_feed_kg: float
    use_until_abw_g: float
    notes: str | None = None


@dataclasses.dataclass
class PricePoint:
    count_size: float
    price_per_kg: float


def _as_float(value, path: str, positive: bool = True) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PredictionError(f"{path} must be a number")
    if positive and number <= 0:
        raise PredictionError(f"{path} must be greater than 0")
    if not positive and number < 0:
        raise PredictionError(f"{path} must be greater than or equal to 0")
    return number


def _decimal(value: float, places: str = "0.001") -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _doc_for(start_date: ddate, target: ddate) -> int:
    return (target - start_date).days + 1


def _date_for_doc(start_date: ddate, doc: int) -> ddate:
    return start_date + timedelta(days=doc - 1)


def _estimated_harvest_count(biomass_kg: Decimal, sampled_abw_g: Decimal) -> int:
    return int(
        ((biomass_kg * Decimal("1000")) / sampled_abw_g).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


async def _farm_and_area(db: AsyncSession, cycle: Cycle) -> tuple[UUID, Decimal]:
    result = await db.execute(
        select(Grid.farm_id, Pond.area_m2)
        .join(Pond, Pond.grid_id == Grid.id)
        .where(Pond.id == cycle.pond_id)
    )
    row = result.one_or_none()
    if row is None:
        raise PredictionError("Pond not found")
    farm_id, area_m2 = row
    if area_m2 is None or area_m2 <= 0:
        raise PredictionError("Pond area is required for prediction")
    return farm_id, area_m2


async def _cumulative_feed_before(db: AsyncSession, cycle_id: UUID, start_date: ddate) -> Decimal:
    result = await db.execute(
        select(func.coalesce(func.sum(FeedingSession.amount_kg), 0))
        .join(DailyLog, FeedingSession.daily_log_id == DailyLog.id)
        .where(DailyLog.cycle_id == cycle_id, DailyLog.date < start_date)
    )
    return Decimal(result.scalar_one())


async def _feed_plan(db: AsyncSession, farm_id: UUID, config_data: dict, cycle: Cycle) -> list[FeedPlanRow]:
    configured = list(config_data.get("feed_plan") or [])
    if not configured:
        result = await db.execute(
            select(FeedType)
            .where(FeedType.farm_id == farm_id)
            .order_by(FeedType.created_at, FeedType.brand)
            .limit(1)
        )
        first = result.scalar_one_or_none()
        if first is None:
            raise PredictionError("At least one feed type is required for prediction")
        configured = [
            {
                "feed_type_id": str(first.id),
                "maximum_daily_feed_kg": float(cycle.maximum_daily_feed_capacity_kg or 65),
                "use_until_abw_g": 999,
            }
        ]

    ids = [UUID(str(row["feed_type_id"])) for row in configured]
    result = await db.execute(
        select(FeedType).where(FeedType.farm_id == farm_id, FeedType.id.in_(ids))
    )
    by_id = {feed.id: feed for feed in result.scalars().all()}
    feed_plan: list[FeedPlanRow] = []
    for index, row in enumerate(configured):
        feed_type_id = UUID(str(row["feed_type_id"]))
        feed_type = by_id.get(feed_type_id)
        if feed_type is None:
            raise PredictionError("Selected feed type is missing from this farm")
        feed_plan.append(
            FeedPlanRow(
                feed_type_id=str(feed_type.id),
                name=f"{feed_type.brand} {feed_type.type}",
                brand=feed_type.brand,
                type=feed_type.type,
                price_per_kg=float(feed_type.price_per_kg),
                maximum_daily_feed_kg=_as_float(
                    row.get("maximum_daily_feed_kg"),
                    f"prediction_config.feed_plan[{index}].maximum_daily_feed_kg",
                ),
                use_until_abw_g=_as_float(
                    row.get("use_until_abw_g"),
                    f"prediction_config.feed_plan[{index}].use_until_abw_g",
                ),
                notes=feed_type.notes,
            )
        )
    return feed_plan


def _price_points(config_data: dict) -> list[PricePoint]:
    rows = (config_data.get("prices") or DEFAULT_CONFIG["prices"]).get("harvest_price_points") or []
    if not rows:
        raise PredictionError("At least one harvest price point is required")
    seen: set[float] = set()
    points = []
    for index, row in enumerate(rows):
        count_size = _as_float(row.get("count_size"), f"prediction_config.prices.harvest_price_points[{index}].count_size")
        if count_size in seen:
            raise PredictionError("Harvest price point count sizes must be unique")
        seen.add(count_size)
        points.append(
            PricePoint(
                count_size=count_size,
                price_per_kg=_as_float(row.get("price_per_kg"), f"prediction_config.prices.harvest_price_points[{index}].price_per_kg"),
            )
        )
    return points


async def build_config(
    db: AsyncSession,
    cycle: Cycle,
    start_date: ddate,
    target_doc: int,
) -> core.Config:
    if start_date < datetime.now(timezone.utc).date():
        raise PredictionError("Prediction start date cannot be in the past")
    start_doc = _doc_for(cycle.start_date, start_date)
    if start_doc < 1:
        raise PredictionError("Prediction start date cannot be before cycle start date")
    if target_doc < start_doc:
        raise PredictionError("Target DOC must be greater than or equal to the prediction start DOC")

    config_data = cycle.prediction_config or {}
    growth = config_data.get("growth") or {}
    target_fcr = _as_float(growth.get("target_fcr", DEFAULT_CONFIG["growth"]["target_fcr"]), "prediction_config.growth.target_fcr")
    baseline = await get_prediction_baseline(db, cycle, start_date)
    starting_population = int(baseline["estimated_population"])
    if starting_population <= 0:
        raise PredictionError("Estimated population must be greater than 0")
    sampled_initial_abw = baseline.get("initial_abw_g")
    if sampled_initial_abw is not None:
        initial_abw_decimal = Decimal(sampled_initial_abw)
        start_biomass = (Decimal(starting_population) * initial_abw_decimal) / Decimal("1000")
    else:
        start_biomass = (
            Decimal(baseline["previous_biomass_kg"])
            + Decimal(baseline["feed_since_previous_sample_start_kg"]) / Decimal(str(target_fcr))
            - Decimal(baseline["harvested_biomass_since_previous_sample_kg"])
        )
    if start_biomass <= 0:
        raise PredictionError("Estimated biomass must be greater than 0")
    initial_abw_g = float((start_biomass * Decimal("1000")) / Decimal(starting_population))
    farm_id, area_m2 = await _farm_and_area(db, cycle)
    feed_plan = await _feed_plan(db, farm_id, config_data, cycle)
    initial_cumulative_feed = await _cumulative_feed_before(db, cycle.id, start_date)

    cycle_settings = config_data.get("cycle") or {}
    costs = config_data.get("costs") or {}
    capacity = config_data.get("capacity") or {}
    harvest = config_data.get("harvest") or {}

    return core.Config(
        pond_area_m2=float(area_m2),
        start_doc=start_doc,
        final_doc=target_doc,
        preparation_day=int(cycle_settings.get("preparation_day", DEFAULT_CONFIG["cycle"]["preparation_day"])),
        starting_population=starting_population,
        initial_abw_g=initial_abw_g,
        maximum_shrimp_size_g=_as_float(cycle_settings.get("maximum_shrimp_size_g", DEFAULT_CONFIG["cycle"]["maximum_shrimp_size_g"]), "prediction_config.cycle.maximum_shrimp_size_g"),
        initial_cumulative_feed_kg=float(initial_cumulative_feed),
        # Backend predictions always run forward from an observed mid-cycle state:
        # the starting ABW/biomass and cumulative feed come from real records, so
        # the model must not re-derive blind-feeding feed or cost.
        total_feed_blind_feeding_kg=0.0,
        blind_feed_additional_cost=0.0,
        past_cost=0,
        observed_state_mode=True,
        target_fcr=target_fcr,
        maximum_adg_g_per_day=_as_float(growth.get("maximum_adg_g_per_day", DEFAULT_CONFIG["growth"]["maximum_adg_g_per_day"]), "prediction_config.growth.maximum_adg_g_per_day"),
        initial_feeding_index=_as_float(growth.get("initial_feeding_index", DEFAULT_CONFIG["growth"]["initial_feeding_index"]), "prediction_config.growth.initial_feeding_index"),
        feeding_index_increment=_as_float(growth.get("feeding_index_increment", cycle.feeding_index_increment or DEFAULT_CONFIG["growth"]["feeding_index_increment"]), "prediction_config.growth.feeding_index_increment"),
        maximum_feeding_index=_as_float(growth.get("maximum_feeding_index", cycle.maximum_feeding_index or DEFAULT_CONFIG["growth"]["maximum_feeding_index"]), "prediction_config.growth.maximum_feeding_index"),
        stable_carrying_capacity_kg_per_m2=_as_float(capacity.get("stable_carrying_capacity_kg_per_m2", DEFAULT_CONFIG["capacity"]["stable_carrying_capacity_kg_per_m2"]), "prediction_config.capacity.stable_carrying_capacity_kg_per_m2"),
        final_carrying_capacity_kg_per_m2=_as_float(capacity.get("final_carrying_capacity_kg_per_m2", DEFAULT_CONFIG["capacity"]["final_carrying_capacity_kg_per_m2"]), "prediction_config.capacity.final_carrying_capacity_kg_per_m2"),
        minimum_partial_harvest_biomass_kg=_as_float(harvest.get("minimum_partial_harvest_biomass_kg", DEFAULT_CONFIG["harvest"]["minimum_partial_harvest_biomass_kg"]), "prediction_config.harvest.minimum_partial_harvest_biomass_kg"),
        minimum_partial_harvest_abw_g=_as_float(harvest.get("minimum_partial_harvest_abw_g", 0), "prediction_config.harvest.minimum_partial_harvest_abw_g", positive=False),
        harvest_fixed_cost_per_event=_as_float(harvest.get("harvest_fixed_cost_per_event", DEFAULT_CONFIG["harvest"]["harvest_fixed_cost_per_event"]), "prediction_config.harvest.harvest_fixed_cost_per_event", positive=False),
        harvest_price_points=_price_points(config_data),
        pl_price_per_piece=_as_float(costs.get("pl_price_per_piece", DEFAULT_CONFIG["costs"]["pl_price_per_piece"]), "prediction_config.costs.pl_price_per_piece", positive=False),
        electricity_kwh=_as_float(costs.get("electricity_kwh", DEFAULT_CONFIG["costs"]["electricity_kwh"]), "prediction_config.costs.electricity_kwh", positive=False),
        electricity_price_per_kwh=_as_float(costs.get("electricity_price_per_kwh", DEFAULT_CONFIG["costs"]["electricity_price_per_kwh"]), "prediction_config.costs.electricity_price_per_kwh", positive=False),
        labor_cost_per_day=_as_float(costs.get("labor_cost_per_day", DEFAULT_CONFIG["costs"]["labor_cost_per_day"]), "prediction_config.costs.labor_cost_per_day", positive=False),
        probiotics_cost_per_day=_as_float(costs.get("probiotics_cost_per_day", DEFAULT_CONFIG["costs"]["probiotics_cost_per_day"]), "prediction_config.costs.probiotics_cost_per_day", positive=False),
        disinfection_cost_per_day=_as_float(costs.get("disinfection_cost_per_day", DEFAULT_CONFIG["costs"]["disinfection_cost_per_day"]), "prediction_config.costs.disinfection_cost_per_day", positive=False),
        liming_cost_per_day=_as_float(costs.get("liming_cost_per_day", DEFAULT_CONFIG["costs"]["liming_cost_per_day"]), "prediction_config.costs.liming_cost_per_day", positive=False),
        feed_plan=feed_plan,
    )


def _feed_type_out(feed: FeedPlanRow) -> list[FeedingFeedType]:
    return [
        FeedingFeedType(
            feed_type_id=feed.feed_type_id,
            brand=feed.brand,
            type=feed.type,
            price_per_kg=Decimal(str(feed.price_per_kg)),
            percentage=Decimal("100"),
            notes=feed.notes,
        )
    ]


def _feedings_for_row(row: core.DailyResult, feed_by_name: dict[str, FeedPlanRow]) -> list[PredictionFeedingOut]:
    feed = feed_by_name.get(row.feed_name)
    if feed is None or row.actual_feed_kg <= 0:
        return []
    feed_types = _feed_type_out(feed)
    return [
        PredictionFeedingOut(
            feed_time=feed_time,
            amount_kg=round_feed_amount_kg(Decimal(str(row.actual_feed_kg)) * fraction),
            feed_types=feed_types,
        )
        for feed_time, fraction in FEEDING_SPLIT
    ]


def _partial_harvest_out(cycle_start_date: ddate, event: core.PartialHarvestEvent) -> PredictionPartialHarvestOut:
    biomass = _decimal(event.kg_harvested)
    abw = _decimal(event.abw_g, "0.0001")
    return PredictionPartialHarvestOut(
        date=_date_for_doc(cycle_start_date, event.doc),
        doc=event.doc,
        biomass_kg=biomass,
        sampled_abw_g=abw,
        count_size=_decimal(event.count_size, "0.01"),
        price_per_kg=_decimal(event.price_per_kg, "0.01"),
        total_price=_decimal(event.revenue, "0.01"),
        estimated_count=_estimated_harvest_count(biomass, abw),
    )


def result_to_out(
    cycle_start_date: ddate,
    result: core.SimulationResult,
    feed_plan: list[FeedPlanRow],
    generated: PredictionGeneratedCounts | None = None,
) -> PredictionResultOut:
    feed_by_name = {feed.name: feed for feed in feed_plan}
    daily_rows = [
        PredictionDailyRowOut(
            date=_date_for_doc(cycle_start_date, row.doc),
            doc=row.doc,
            feed_name=row.feed_name,
            feeding_index=_decimal(row.feeding_index, "0.0001"),
            starting_population=int(round(row.starting_population)),
            ending_population=int(round(row.ending_population)),
            starting_abw_g=_decimal(row.starting_abw_g, "0.0001"),
            ending_abw_g=_decimal(row.ending_abw_g, "0.0001"),
            starting_biomass_kg=_decimal(row.starting_biomass_kg),
            ending_biomass_kg=_decimal(row.ending_biomass_kg),
            actual_feed_kg=sum((feeding.amount_kg for feeding in _feedings_for_row(row, feed_by_name)), Decimal("0")),
            cumulative_feed_kg=_decimal(row.cumulative_feed_kg),
            count_size=_decimal(row.count_size, "0.01"),
            harvest_price_per_kg=_decimal(row.harvest_price_per_kg, "0.01"),
            partial_harvest_kg=_decimal(row.partial_harvest_kg),
            stop_reason=row.stop_reason,
            feedings=_feedings_for_row(row, feed_by_name),
        )
        for row in result.daily_results
    ]
    partials = [_partial_harvest_out(cycle_start_date, event) for event in result.partial_harvests]
    total_harvested = result.final_biomass_kg + sum(event.kg_harvested for event in result.partial_harvests)
    initial_abw_g = result.daily_results[0].starting_abw_g if result.daily_results else result.final_abw_g
    return PredictionResultOut(
        summary=PredictionSummaryOut(
            initial_abw_g=_decimal(initial_abw_g, "0.0001"),
            final_doc=result.final_doc,
            final_date=_date_for_doc(cycle_start_date, result.final_doc),
            final_abw_g=_decimal(result.final_abw_g, "0.0001"),
            final_biomass_kg=_decimal(result.final_biomass_kg),
            total_harvested_biomass_kg=_decimal(total_harvested),
            cumulative_feed_kg=_decimal(result.cumulative_feed_kg),
            simulated_feed_kg=sum((row.actual_feed_kg for row in daily_rows), Decimal("0")),
            final_revenue=_decimal(result.final_revenue, "0.01"),
            partial_revenue=_decimal(result.partial_revenue, "0.01"),
            total_revenue=_decimal(result.total_revenue, "0.01"),
            feed_cost=_decimal(result.feed_cost, "0.01"),
            total_costs=_decimal(result.total_costs, "0.01"),
            profit=_decimal(result.profit, "0.01"),
            profit_per_day=_decimal(result.profit_per_day, "0.01"),
            harvest_count_size=_decimal(result.harvest_count_size, "0.01"),
            harvest_price_per_kg=_decimal(result.harvest_price_per_kg, "0.01"),
            stop_reason=result.stop_reason,
        ),
        daily_rows=daily_rows,
        partial_harvests=partials,
        generated=generated,
    )


async def preview_prediction(
    db: AsyncSession,
    cycle: Cycle,
    start_date: ddate,
    target_doc: int,
    optimize: bool,
) -> PredictionResultOut:
    config = await build_config(db, cycle, start_date, target_doc)
    runner = core.optimize_partial_harvests if optimize else core.simulate
    result = await asyncio.to_thread(runner, config)
    return result_to_out(cycle.start_date, result, config.feed_plan)


async def generate_prediction(
    db: AsyncSession,
    cycle: Cycle,
    start_date: ddate,
    target_doc: int,
    optimize: bool,
) -> PredictionResultOut:
    preview = await preview_prediction(db, cycle, start_date, target_doc, optimize)
    return await apply_prediction_result(db, cycle, preview)


async def apply_prediction_result(
    db: AsyncSession,
    cycle: Cycle,
    preview: PredictionResultOut,
) -> PredictionResultOut:
    target_dates = {row.date for row in preview.daily_rows}
    first_target_date = min(target_dates)

    existing_result = await db.execute(
        select(DailyLog).where(DailyLog.cycle_id == cycle.id, DailyLog.date >= first_target_date)
    )
    existing_logs = list(existing_result.scalars().all())
    existing_logs_by_date = {log.date: log for log in existing_logs}
    preserved_start_log = existing_logs_by_date.get(first_target_date)
    preserved_start_abw = preserved_start_log is not None and preserved_start_log.abw_g is not None
    existing_log_ids = [log.id for log in existing_logs]
    deleted_log_ids = [
        log.id
        for log in existing_logs
        if preserved_start_log is None or log.id != preserved_start_log.id
    ]
    daily_logs_deleted = len(deleted_log_ids)
    if existing_log_ids:
        await db.execute(delete(FeedingSession).where(FeedingSession.daily_log_id.in_(existing_log_ids)))
        await db.execute(delete(WaterParameters).where(WaterParameters.daily_log_id.in_(existing_log_ids)))
        await db.execute(delete(Harvest).where(Harvest.daily_log_id.in_(existing_log_ids)))
        await db.execute(delete(Treatment).where(Treatment.daily_log_id.in_(existing_log_ids)))
    if deleted_log_ids:
        await db.execute(delete(DailyLog).where(DailyLog.id.in_(deleted_log_ids)))
    await db.execute(
        delete(PopulationSample).where(
            PopulationSample.cycle_id == cycle.id,
            PopulationSample.date >= first_target_date,
        )
    )

    logs_by_date: dict[ddate, DailyLog] = {}
    feedings_created = 0
    harvests_created = 0
    partials_by_date = {partial.date: partial for partial in preview.partial_harvests}
    final_date = preview.summary.final_date

    for row in preview.daily_rows:
        if row.date == first_target_date and preserved_start_log is not None:
            log = preserved_start_log
        else:
            log = DailyLog(cycle_id=cycle.id, date=row.date)
            db.add(log)
        # Stamp an ABW sample so daily-metrics interpolation has a hard anchor at
        # every partial harvest and at the final day. Anchoring each harvest keeps
        # the interpolated ABW between samples from drifting across harvest boundaries.
        if not (row.date == first_target_date and preserved_start_abw):
            partial = partials_by_date.get(row.date)
            if row.date == final_date:
                log.abw_g = row.ending_abw_g
                log.abw_sample_time = HARVEST_TIME
            elif partial is not None:
                log.abw_g = partial.sampled_abw_g
                log.abw_sample_time = HARVEST_TIME
        logs_by_date[row.date] = log

    await db.flush()

    for row in preview.daily_rows:
        log = logs_by_date[row.date]
        partial = partials_by_date.get(row.date)
        if partial:
            db.add(
                Harvest(
                    daily_log_id=log.id,
                    harvest_time=HARVEST_TIME,
                    biomass_kg=partial.biomass_kg,
                    sampled_abw_g=partial.sampled_abw_g,
                    total_price=partial.total_price,
                    estimated_count=partial.estimated_count,
                    notes="Predicted partial harvest",
                )
            )
            harvests_created += 1
        for feeding in row.feedings:
            db.add(
                FeedingSession(
                    daily_log_id=log.id,
                    feed_time=feeding.feed_time,
                    amount_kg=feeding.amount_kg,
                    additives=[],
                    feed_types=[item.model_dump(mode="json") for item in feeding.feed_types],
                    notes="Predicted feeding",
                )
            )
            feedings_created += 1

    # Harvest the remaining standing crop on the last prediction day.
    final_log = logs_by_date.get(final_date)
    if final_log is not None and preview.summary.final_biomass_kg > 0:
        final_biomass = preview.summary.final_biomass_kg
        final_abw = preview.summary.final_abw_g
        db.add(
            Harvest(
                daily_log_id=final_log.id,
                harvest_time=HARVEST_TIME,
                biomass_kg=final_biomass,
                sampled_abw_g=final_abw,
                total_price=preview.summary.final_revenue,
                estimated_count=_estimated_harvest_count(final_biomass, final_abw),
                notes="Predicted final harvest",
            )
        )
        harvests_created += 1

    await db.commit()
    counts = PredictionGeneratedCounts(
        days=len(preview.daily_rows),
        feedings_created=feedings_created,
        harvests_created=harvests_created,
        daily_logs_deleted=daily_logs_deleted,
    )
    return PredictionResultOut(
        summary=preview.summary,
        daily_rows=preview.daily_rows,
        partial_harvests=preview.partial_harvests,
        generated=counts,
    )
