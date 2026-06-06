import asyncio
import dataclasses
import math
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
from app.services.day_view import get_prediction_baseline
from app.services.feeding_amounts import round_feed_amount_kg

PARTIAL_HARVEST_STEP_KG = 25
MAX_OPTIMIZER_STATES_PER_DOC = 5000
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


@dataclasses.dataclass
class Config:
    pond_area_m2: float
    start_doc: int
    final_doc: int
    preparation_day: int
    starting_population: int
    initial_abw_g: float
    maximum_shrimp_size_g: float
    initial_cumulative_feed_kg: float
    past_cost: float
    target_fcr: float
    maximum_adg_g_per_day: float
    initial_feeding_index: float
    feeding_index_increment: float
    maximum_feeding_index: float
    stable_carrying_capacity_kg_per_m2: float
    final_carrying_capacity_kg_per_m2: float
    minimum_partial_harvest_biomass_kg: float
    harvest_fixed_cost_per_event: float
    harvest_price_points: list[PricePoint]
    pl_price_per_piece: float
    electricity_kwh: float
    electricity_price_per_kwh: float
    labor_cost_per_day: float
    probiotics_cost_per_day: float
    disinfection_cost_per_day: float
    liming_cost_per_day: float
    feed_plan: list[FeedPlanRow]


@dataclasses.dataclass
class PartialHarvestEvent:
    doc: int
    kg_harvested: float
    abw_g: float
    count_size: float
    price_per_kg: float
    revenue: float
    fixed_cost: float
    population_removed: float


@dataclasses.dataclass
class DailyResult:
    doc: int
    starting_population: float
    ending_population: float
    feed: FeedPlanRow | None
    feeding_index: float
    starting_abw_g: float
    ending_abw_g: float
    starting_biomass_kg: float
    ending_biomass_kg: float
    actual_feed_kg: float
    cumulative_feed_kg: float
    count_size: float
    harvest_price_per_kg: float
    partial_event: PartialHarvestEvent | None
    stable_locked: bool
    feed_cost: float
    stop_reason: str


@dataclasses.dataclass
class SimulationResult:
    final_doc: int
    final_abw_g: float
    final_biomass_kg: float
    cumulative_feed_kg: float
    simulated_feed_kg: float
    final_revenue: float
    partial_revenue: float
    total_revenue: float
    feed_cost: float
    total_costs: float
    profit: float
    profit_per_day: float
    harvest_count_size: float
    harvest_price_per_kg: float
    stop_reason: str
    partial_harvests: list[PartialHarvestEvent]
    daily_results: list[DailyResult]


@dataclasses.dataclass(frozen=True)
class OptimizerState:
    doc: int
    population: float
    biomass_kg: float
    cumulative_feed_kg: float
    simulated_feed_kg: float
    feed_cost: float
    partial_revenue: float
    partial_harvest_cost: float
    stable_locked: bool
    daily_results: tuple[DailyResult, ...]
    partial_harvests: tuple[PartialHarvestEvent, ...]


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


def _get(data: dict, group: str, key: str):
    return (data.get(group) or DEFAULT_CONFIG.get(group, {})).get(
        key, DEFAULT_CONFIG.get(group, {}).get(key)
    )


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


def selected_feed_for_abw(feed_plan: list[FeedPlanRow], abw_g: float) -> FeedPlanRow:
    for row in feed_plan:
        if abw_g <= row.use_until_abw_g:
            return row
    return feed_plan[-1]


def interpolate_price(price_points: list[PricePoint], count_size: float) -> float:
    sorted_points = sorted(price_points, key=lambda point: point.count_size)
    if count_size <= sorted_points[0].count_size:
        return sorted_points[0].price_per_kg
    if count_size >= sorted_points[-1].count_size:
        return sorted_points[-1].price_per_kg

    for index in range(1, len(sorted_points)):
        lower = sorted_points[index - 1]
        upper = sorted_points[index]
        if count_size <= upper.count_size:
            distance = (count_size - lower.count_size) / (upper.count_size - lower.count_size)
            return lower.price_per_kg + distance * (upper.price_per_kg - lower.price_per_kg)
    return sorted_points[-1].price_per_kg


def feeding_index_for_doc(config: Config, doc: int) -> float:
    elapsed_days = doc - config.start_doc
    return min(
        config.maximum_feeding_index,
        config.initial_feeding_index + config.feeding_index_increment * elapsed_days,
    )


def stable_capacity_kg(config: Config) -> float:
    return config.pond_area_m2 * config.stable_carrying_capacity_kg_per_m2


def final_capacity_kg(config: Config) -> float:
    return config.pond_area_m2 * config.final_carrying_capacity_kg_per_m2


def daily_cost_totals(config: Config, production_day_count: int) -> float:
    electricity_daily_cost = config.electricity_kwh * 24 * config.electricity_price_per_kwh
    return (
        electricity_daily_cost
        + config.labor_cost_per_day
        + config.probiotics_cost_per_day
        + config.disinfection_cost_per_day
        + config.liming_cost_per_day
    ) * production_day_count


def run_daily_step(
    config: Config,
    doc: int,
    population: float,
    biomass_kg: float,
    cumulative_feed_kg: float,
    simulated_feed_kg: float,
    feed_cost: float,
    stable_locked: bool,
    day_start_population: float | None = None,
    day_start_biomass_kg: float | None = None,
    partial_event: PartialHarvestEvent | None = None,
):
    day_start_population = population if day_start_population is None else day_start_population
    day_start_biomass_kg = biomass_kg if day_start_biomass_kg is None else day_start_biomass_kg
    starting_abw_g = day_start_biomass_kg * 1000 / day_start_population
    current_abw_g = biomass_kg * 1000 / population
    feed = selected_feed_for_abw(config.feed_plan, current_abw_g)
    feeding_index = feeding_index_for_doc(config, doc)
    maximum_size_biomass_kg = population * config.maximum_shrimp_size_g / 1000
    remaining_growth_to_max_size_kg = max(0, maximum_size_biomass_kg - biomass_kg)

    feed_from_index = feeding_index * doc * population / 100000
    feed_from_type_limit = feed.maximum_daily_feed_kg
    feed_from_adg_limit = population * config.maximum_adg_g_per_day / 1000 * config.target_fcr
    feed_from_size_limit = remaining_growth_to_max_size_kg * config.target_fcr
    actual_feed_kg = min(
        feed_from_index,
        feed_from_type_limit,
        feed_from_adg_limit,
        feed_from_size_limit,
    )

    biomass_gain_kg = actual_feed_kg / config.target_fcr
    biomass_kg += biomass_gain_kg
    ending_abw_g = biomass_kg * 1000 / population
    count_size = 1000 / ending_abw_g
    harvest_price_per_kg = interpolate_price(config.harvest_price_points, count_size)
    cumulative_feed_kg += actual_feed_kg
    simulated_feed_kg += actual_feed_kg
    daily_feed_cost = actual_feed_kg * feed.price_per_kg
    feed_cost += daily_feed_cost
    stable_locked_after_day = stable_locked or biomass_kg >= stable_capacity_kg(config)

    day_stop_reason = ""
    if biomass_kg >= final_capacity_kg(config):
        day_stop_reason = "final_carrying_capacity"
    elif ending_abw_g >= config.maximum_shrimp_size_g:
        day_stop_reason = "maximum_shrimp_size"

    row = DailyResult(
        doc=doc,
        starting_population=day_start_population,
        ending_population=population,
        feed=feed,
        feeding_index=feeding_index,
        starting_abw_g=starting_abw_g,
        ending_abw_g=ending_abw_g,
        starting_biomass_kg=day_start_biomass_kg,
        ending_biomass_kg=biomass_kg,
        actual_feed_kg=actual_feed_kg,
        cumulative_feed_kg=cumulative_feed_kg,
        count_size=count_size,
        harvest_price_per_kg=harvest_price_per_kg,
        partial_event=partial_event,
        stable_locked=stable_locked_after_day,
        feed_cost=daily_feed_cost,
        stop_reason=day_stop_reason,
    )
    return population, biomass_kg, cumulative_feed_kg, simulated_feed_kg, feed_cost, row, day_stop_reason, stable_locked_after_day


def terminal_day(
    config: Config,
    doc: int,
    population: float,
    biomass_kg: float,
    cumulative_feed_kg: float,
    stable_locked: bool,
) -> DailyResult:
    abw_g = biomass_kg * 1000 / population
    count_size = 1000 / abw_g
    return DailyResult(
        doc=doc,
        starting_population=population,
        ending_population=population,
        feed=None,
        feeding_index=0,
        starting_abw_g=abw_g,
        ending_abw_g=abw_g,
        starting_biomass_kg=biomass_kg,
        ending_biomass_kg=biomass_kg,
        actual_feed_kg=0,
        cumulative_feed_kg=cumulative_feed_kg,
        count_size=count_size,
        harvest_price_per_kg=interpolate_price(config.harvest_price_points, count_size),
        partial_event=None,
        stable_locked=stable_locked,
        feed_cost=0,
        stop_reason="final_doc",
    )


def finalize_simulation(
    config: Config,
    final_doc: int,
    population: float,
    biomass_kg: float,
    cumulative_feed_kg: float,
    simulated_feed_kg: float,
    feed_cost: float,
    partial_revenue: float,
    partial_harvest_cost: float,
    partial_harvests: tuple[PartialHarvestEvent, ...],
    daily_results: tuple[DailyResult, ...],
    stop_reason: str,
) -> SimulationResult:
    final_abw_g = biomass_kg * 1000 / population
    harvest_count_size = 1000 / final_abw_g
    harvest_price_per_kg = interpolate_price(config.harvest_price_points, harvest_count_size)
    final_revenue = biomass_kg * harvest_price_per_kg
    total_revenue = partial_revenue + final_revenue
    total_harvest_event_cost = partial_harvest_cost + config.harvest_fixed_cost_per_event
    total_costs = config.past_cost + feed_cost + daily_cost_totals(config, len(daily_results)) + total_harvest_event_cost
    profit = total_revenue - total_costs
    profit_per_day = profit / max(config.preparation_day + final_doc, 1)
    return SimulationResult(
        final_doc=final_doc,
        final_abw_g=final_abw_g,
        final_biomass_kg=biomass_kg,
        cumulative_feed_kg=cumulative_feed_kg,
        simulated_feed_kg=simulated_feed_kg,
        final_revenue=final_revenue,
        partial_revenue=partial_revenue,
        total_revenue=total_revenue,
        feed_cost=feed_cost,
        total_costs=total_costs,
        profit=profit,
        profit_per_day=profit_per_day,
        harvest_count_size=harvest_count_size,
        harvest_price_per_kg=harvest_price_per_kg,
        stop_reason=stop_reason,
        partial_harvests=list(partial_harvests),
        daily_results=list(daily_results),
    )


def generate_partial_harvest_candidates(config: Config, biomass_kg: float) -> list[int]:
    first_candidate = int(
        math.ceil(config.minimum_partial_harvest_biomass_kg / PARTIAL_HARVEST_STEP_KG)
        * PARTIAL_HARVEST_STEP_KG
    )
    max_candidate = int(math.floor((biomass_kg - 1e-9) / PARTIAL_HARVEST_STEP_KG) * PARTIAL_HARVEST_STEP_KG)
    if max_candidate < first_candidate:
        return []
    return list(range(first_candidate, max_candidate + PARTIAL_HARVEST_STEP_KG, PARTIAL_HARVEST_STEP_KG))


def apply_partial_harvest(config: Config, doc: int, population: float, biomass_kg: float, harvest_kg: float):
    abw_g = biomass_kg * 1000 / population
    population_removed = harvest_kg * 1000 / abw_g
    population_after_harvest = population - population_removed
    biomass_after_harvest_kg = biomass_kg - harvest_kg
    if population_after_harvest <= 0 or biomass_after_harvest_kg <= 0:
        return None
    count_size = 1000 / abw_g
    price_per_kg = interpolate_price(config.harvest_price_points, count_size)
    event = PartialHarvestEvent(
        doc=doc,
        kg_harvested=harvest_kg,
        abw_g=abw_g,
        count_size=count_size,
        price_per_kg=price_per_kg,
        revenue=harvest_kg * price_per_kg,
        fixed_cost=config.harvest_fixed_cost_per_event,
        population_removed=population_removed,
    )
    return population_after_harvest, biomass_after_harvest_kg, event


def current_liquidation_value_from_values(config: Config, population: float, biomass_kg: float, partial_revenue: float, feed_cost: float, partial_harvest_cost: float) -> float:
    abw_g = biomass_kg * 1000 / population
    count_size = 1000 / abw_g
    standing_revenue = biomass_kg * interpolate_price(config.harvest_price_points, count_size)
    return partial_revenue + standing_revenue - feed_cost - partial_harvest_cost


def optimizer_bucket_key_from_values(doc: int, stable_locked: bool, population: float, biomass_kg: float):
    rounded_population = int(round(population / 250) * 250)
    rounded_biomass = int(round(biomass_kg / 5) * 5)
    return doc, stable_locked, rounded_population, rounded_biomass


def optimizer_state_rank_from_values(
    config: Config,
    population: float,
    biomass_kg: float,
    partial_revenue: float,
    feed_cost: float,
    partial_harvest_cost: float,
    partial_harvest_count: int,
):
    return (
        current_liquidation_value_from_values(
            config, population, biomass_kg, partial_revenue, feed_cost, partial_harvest_cost
        ),
        -partial_harvest_count,
        biomass_kg,
        population,
    )


def limit_optimizer_states(config: Config, states: list[OptimizerState]) -> list[OptimizerState]:
    if len(states) > MAX_OPTIMIZER_STATES_PER_DOC:
        states.sort(
            key=lambda state: optimizer_state_rank_from_values(
                config,
                state.population,
                state.biomass_kg,
                state.partial_revenue,
                state.feed_cost,
                state.partial_harvest_cost,
                len(state.partial_harvests),
            ),
            reverse=True,
        )
        states = states[:MAX_OPTIMIZER_STATES_PER_DOC]
    return states


def result_rank(result: SimulationResult):
    return result.profit_per_day, result.profit, result.final_abw_g, -len(result.partial_harvests)


def better_result(candidate: SimulationResult, current: SimulationResult | None) -> bool:
    return current is None or result_rank(candidate) > result_rank(current)


def simulate(config: Config) -> SimulationResult:
    population = config.starting_population
    biomass_kg = population * config.initial_abw_g / 1000
    cumulative_feed_kg = config.initial_cumulative_feed_kg
    simulated_feed_kg = 0
    feed_cost = 0
    stable_locked = biomass_kg >= stable_capacity_kg(config)
    daily_results: tuple[DailyResult, ...] = ()

    for doc in range(config.start_doc, config.final_doc + 1):
        if doc == config.final_doc:
            row = terminal_day(config, doc, population, biomass_kg, cumulative_feed_kg, stable_locked)
            daily_results += (row,)
            return finalize_simulation(
                config, doc, population, biomass_kg, cumulative_feed_kg, simulated_feed_kg,
                feed_cost, 0, 0, (), daily_results, "final_doc"
            )
        population, biomass_kg, cumulative_feed_kg, simulated_feed_kg, feed_cost, row, stop_reason, stable_locked = run_daily_step(
            config, doc, population, biomass_kg, cumulative_feed_kg, simulated_feed_kg, feed_cost, stable_locked
        )
        daily_results += (row,)
        if stop_reason:
            return finalize_simulation(
                config, doc, population, biomass_kg, cumulative_feed_kg, simulated_feed_kg,
                feed_cost, 0, 0, (), daily_results, stop_reason
            )

    raise PredictionError("prediction produced no daily rows")


def optimize_partial_harvests(config: Config) -> SimulationResult:
    population = config.starting_population
    biomass_kg = population * config.initial_abw_g / 1000
    states = [
        OptimizerState(
            doc=config.start_doc,
            population=population,
            biomass_kg=biomass_kg,
            cumulative_feed_kg=config.initial_cumulative_feed_kg,
            simulated_feed_kg=0,
            feed_cost=0,
            partial_revenue=0,
            partial_harvest_cost=0,
            stable_locked=biomass_kg >= stable_capacity_kg(config),
            daily_results=(),
            partial_harvests=(),
        )
    ]
    best: SimulationResult | None = None
    stable_capacity_limit_kg = stable_capacity_kg(config)

    for doc in range(config.start_doc, config.final_doc + 1):
        if doc == config.final_doc:
            for state in states:
                row = terminal_day(
                    config, doc, state.population, state.biomass_kg, state.cumulative_feed_kg, state.stable_locked
                )
                candidate = finalize_simulation(
                    config=config,
                    final_doc=doc,
                    population=state.population,
                    biomass_kg=state.biomass_kg,
                    cumulative_feed_kg=state.cumulative_feed_kg,
                    simulated_feed_kg=state.simulated_feed_kg,
                    feed_cost=state.feed_cost,
                    partial_revenue=state.partial_revenue,
                    partial_harvest_cost=state.partial_harvest_cost,
                    partial_harvests=state.partial_harvests,
                    daily_results=state.daily_results + (row,),
                    stop_reason="final_doc",
                )
                if better_result(candidate, best):
                    best = candidate
            break

        next_best_by_bucket: dict[tuple, OptimizerState] = {}
        next_rank_by_bucket: dict[tuple, tuple] = {}
        for state in states:
            state_stable_locked = state.stable_locked or state.biomass_kg >= stable_capacity_limit_kg
            harvest_choices: list[int | None] = [None]
            if not state_stable_locked and state.biomass_kg < stable_capacity_limit_kg:
                harvest_choices.extend(generate_partial_harvest_candidates(config, state.biomass_kg))

            for harvest_kg in harvest_choices:
                day_start_population = state.population
                day_start_biomass_kg = state.biomass_kg
                population_after_harvest = state.population
                biomass_after_harvest_kg = state.biomass_kg
                partial_event = None
                partial_revenue = state.partial_revenue
                partial_harvest_cost = state.partial_harvest_cost
                partial_harvests = state.partial_harvests

                if harvest_kg is not None:
                    applied = apply_partial_harvest(config, doc, state.population, state.biomass_kg, harvest_kg)
                    if applied is None:
                        continue
                    population_after_harvest, biomass_after_harvest_kg, partial_event = applied
                    partial_revenue += partial_event.revenue
                    partial_harvest_cost += partial_event.fixed_cost
                    partial_harvests = state.partial_harvests + (partial_event,)

                next_population, next_biomass_kg, next_cumulative_feed_kg, next_simulated_feed_kg, next_feed_cost, daily_result, day_stop_reason, next_stable_locked = run_daily_step(
                    config,
                    doc,
                    population_after_harvest,
                    biomass_after_harvest_kg,
                    state.cumulative_feed_kg,
                    state.simulated_feed_kg,
                    state.feed_cost,
                    state_stable_locked,
                    day_start_population=day_start_population,
                    day_start_biomass_kg=day_start_biomass_kg,
                    partial_event=partial_event,
                )

                if day_stop_reason:
                    candidate = finalize_simulation(
                        config=config,
                        final_doc=doc,
                        population=next_population,
                        biomass_kg=next_biomass_kg,
                        cumulative_feed_kg=next_cumulative_feed_kg,
                        simulated_feed_kg=next_simulated_feed_kg,
                        feed_cost=next_feed_cost,
                        partial_revenue=partial_revenue,
                        partial_harvest_cost=partial_harvest_cost,
                        partial_harvests=partial_harvests,
                        daily_results=state.daily_results + (daily_result,),
                        stop_reason=day_stop_reason,
                    )
                    if better_result(candidate, best):
                        best = candidate
                else:
                    key = optimizer_bucket_key_from_values(
                        doc + 1, next_stable_locked, next_population, next_biomass_kg
                    )
                    rank = optimizer_state_rank_from_values(
                        config,
                        next_population,
                        next_biomass_kg,
                        partial_revenue,
                        next_feed_cost,
                        partial_harvest_cost,
                        len(partial_harvests),
                    )
                    if key not in next_best_by_bucket or rank > next_rank_by_bucket[key]:
                        next_best_by_bucket[key] = OptimizerState(
                            doc=doc + 1,
                            population=next_population,
                            biomass_kg=next_biomass_kg,
                            cumulative_feed_kg=next_cumulative_feed_kg,
                            simulated_feed_kg=next_simulated_feed_kg,
                            feed_cost=next_feed_cost,
                            partial_revenue=partial_revenue,
                            partial_harvest_cost=partial_harvest_cost,
                            stable_locked=next_stable_locked,
                            daily_results=state.daily_results + (daily_result,),
                            partial_harvests=partial_harvests,
                        )
                        next_rank_by_bucket[key] = rank
        states = limit_optimizer_states(config, list(next_best_by_bucket.values()))
        if not states:
            break

    return best or simulate(config)


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
) -> Config:
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

    return Config(
        pond_area_m2=float(area_m2),
        start_doc=start_doc,
        final_doc=target_doc,
        preparation_day=int(cycle_settings.get("preparation_day", DEFAULT_CONFIG["cycle"]["preparation_day"])),
        starting_population=starting_population,
        initial_abw_g=initial_abw_g,
        maximum_shrimp_size_g=_as_float(cycle_settings.get("maximum_shrimp_size_g", DEFAULT_CONFIG["cycle"]["maximum_shrimp_size_g"]), "prediction_config.cycle.maximum_shrimp_size_g"),
        initial_cumulative_feed_kg=float(initial_cumulative_feed),
        past_cost=0,
        target_fcr=target_fcr,
        maximum_adg_g_per_day=_as_float(growth.get("maximum_adg_g_per_day", DEFAULT_CONFIG["growth"]["maximum_adg_g_per_day"]), "prediction_config.growth.maximum_adg_g_per_day"),
        initial_feeding_index=_as_float(growth.get("initial_feeding_index", DEFAULT_CONFIG["growth"]["initial_feeding_index"]), "prediction_config.growth.initial_feeding_index"),
        feeding_index_increment=_as_float(growth.get("feeding_index_increment", cycle.feeding_index_increment or DEFAULT_CONFIG["growth"]["feeding_index_increment"]), "prediction_config.growth.feeding_index_increment"),
        maximum_feeding_index=_as_float(growth.get("maximum_feeding_index", cycle.maximum_feeding_index or DEFAULT_CONFIG["growth"]["maximum_feeding_index"]), "prediction_config.growth.maximum_feeding_index"),
        stable_carrying_capacity_kg_per_m2=_as_float(capacity.get("stable_carrying_capacity_kg_per_m2", DEFAULT_CONFIG["capacity"]["stable_carrying_capacity_kg_per_m2"]), "prediction_config.capacity.stable_carrying_capacity_kg_per_m2"),
        final_carrying_capacity_kg_per_m2=_as_float(capacity.get("final_carrying_capacity_kg_per_m2", DEFAULT_CONFIG["capacity"]["final_carrying_capacity_kg_per_m2"]), "prediction_config.capacity.final_carrying_capacity_kg_per_m2"),
        minimum_partial_harvest_biomass_kg=_as_float(harvest.get("minimum_partial_harvest_biomass_kg", DEFAULT_CONFIG["harvest"]["minimum_partial_harvest_biomass_kg"]), "prediction_config.harvest.minimum_partial_harvest_biomass_kg"),
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


def _feedings_for_row(row: DailyResult) -> list[PredictionFeedingOut]:
    if row.feed is None or row.actual_feed_kg <= 0:
        return []
    feed_types = _feed_type_out(row.feed)
    return [
        PredictionFeedingOut(
            feed_time=feed_time,
            amount_kg=round_feed_amount_kg(Decimal(str(row.actual_feed_kg)) * fraction),
            feed_types=feed_types,
        )
        for feed_time, fraction in FEEDING_SPLIT
    ]


def _partial_harvest_out(cycle_start_date: ddate, event: PartialHarvestEvent) -> PredictionPartialHarvestOut:
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


def result_to_out(cycle_start_date: ddate, result: SimulationResult, generated: PredictionGeneratedCounts | None = None) -> PredictionResultOut:
    daily_rows = [
        PredictionDailyRowOut(
            date=_date_for_doc(cycle_start_date, row.doc),
            doc=row.doc,
            feed_name=row.feed.name if row.feed else "",
            feeding_index=_decimal(row.feeding_index, "0.0001"),
            starting_population=int(round(row.starting_population)),
            ending_population=int(round(row.ending_population)),
            starting_abw_g=_decimal(row.starting_abw_g, "0.0001"),
            ending_abw_g=_decimal(row.ending_abw_g, "0.0001"),
            starting_biomass_kg=_decimal(row.starting_biomass_kg),
            ending_biomass_kg=_decimal(row.ending_biomass_kg),
            actual_feed_kg=sum((feeding.amount_kg for feeding in _feedings_for_row(row)), Decimal("0")),
            cumulative_feed_kg=_decimal(row.cumulative_feed_kg),
            count_size=_decimal(row.count_size, "0.01"),
            harvest_price_per_kg=_decimal(row.harvest_price_per_kg, "0.01"),
            partial_harvest_kg=_decimal(row.partial_event.kg_harvested if row.partial_event else 0),
            stop_reason=row.stop_reason,
            feedings=_feedings_for_row(row),
        )
        for row in result.daily_results
    ]
    partials = [_partial_harvest_out(cycle_start_date, event) for event in result.partial_harvests]
    total_harvested = result.final_biomass_kg + sum(event.kg_harvested for event in result.partial_harvests)
    return PredictionResultOut(
        summary=PredictionSummaryOut(
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
    runner = optimize_partial_harvests if optimize else simulate
    result = await asyncio.to_thread(runner, config)
    return result_to_out(cycle.start_date, result)


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
        select(DailyLog.id).where(DailyLog.cycle_id == cycle.id, DailyLog.date >= first_target_date)
    )
    existing_log_ids = list(existing_result.scalars().all())
    daily_logs_deleted = len(existing_log_ids)
    if existing_log_ids:
        await db.execute(delete(FeedingSession).where(FeedingSession.daily_log_id.in_(existing_log_ids)))
        await db.execute(delete(WaterParameters).where(WaterParameters.daily_log_id.in_(existing_log_ids)))
        await db.execute(delete(Harvest).where(Harvest.daily_log_id.in_(existing_log_ids)))
        await db.execute(delete(Treatment).where(Treatment.daily_log_id.in_(existing_log_ids)))
        await db.execute(delete(DailyLog).where(DailyLog.id.in_(existing_log_ids)))
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
        log = DailyLog(cycle_id=cycle.id, date=row.date)
        if row.date == final_date:
            log.abw_g = row.ending_abw_g
            log.abw_sample_time = HARVEST_TIME
        db.add(log)
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
