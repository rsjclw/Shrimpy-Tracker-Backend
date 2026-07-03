import argparse
import csv
import dataclasses
import json
import math


PARTIAL_HARVEST_STEP_KG = 25
MAX_OPTIMIZER_STATES_PER_DOC = 5000


@dataclasses.dataclass
class FeedPlanRow:
    name: str
    price_per_kg: float
    maximum_daily_feed_kg: float
    use_until_abw_g: float


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
    total_feed_blind_feeding_kg: float
    blind_feed_additional_cost: float
    past_cost: float
    observed_state_mode: bool
    target_fcr: float
    maximum_adg_g_per_day: float
    initial_feeding_index: float
    feeding_index_increment: float
    maximum_feeding_index: float
    stable_carrying_capacity_kg_per_m2: float
    final_carrying_capacity_kg_per_m2: float
    minimum_partial_harvest_biomass_kg: float
    minimum_partial_harvest_abw_g: float
    harvest_fixed_cost_per_event: float
    harvest_price_points: list
    pl_price_per_piece: float
    electricity_kwh: float
    electricity_price_per_kwh: float
    labor_cost_per_day: float
    probiotics_cost_per_day: float
    disinfection_cost_per_day: float
    liming_cost_per_day: float
    feed_plan: list


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
    feed_name: str
    feeding_index: float
    starting_abw_g: float
    ending_abw_g: float
    starting_biomass_kg: float
    ending_biomass_kg: float
    feed_from_index_kg: float
    feed_from_type_limit_kg: float
    feed_from_adg_limit_kg: float
    feed_from_size_limit_kg: float
    actual_feed_kg: float
    biomass_gain_kg: float
    adg_g_per_day: float
    cumulative_feed_kg: float
    count_size: float
    harvest_price_per_kg: float
    partial_harvest_kg: float
    partial_harvest_revenue: float
    partial_harvest_price_per_kg: float
    partial_harvest_population_removed: float
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
    past_cost: float
    observed_state_mode: bool
    pl_cost: float
    feed_cost: float
    preparation_cost: float
    blind_feeding_daily_costs: float
    production_daily_costs: float
    labor_cost: float
    electricity_cost: float
    probiotics_cost: float
    disinfection_cost: float
    liming_cost: float
    daily_costs: float
    harvest_cost: float
    total_harvest_event_cost: float
    total_costs: float
    profit: float
    profit_per_day: float
    harvest_count_size: float
    harvest_price_per_kg: float
    stop_reason: str
    partial_harvests: list
    daily_results: list


def positive(value, path):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{path} must be a positive number")
    return value


def non_negative(value, path):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{path} must be a non-negative number")
    return value


def integer_positive(value, path):
    positive(value, path)
    if int(value) != value:
        raise ValueError(f"{path} must be an integer")
    return int(value)


def integer_non_negative(value, path):
    non_negative(value, path)
    if int(value) != value:
        raise ValueError(f"{path} must be an integer")
    return int(value)


def required(data, path):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"{path} is required")
        current = current[part]
    return current


def load_config(data):
    cycle = required(data, "cycle")
    growth = required(data, "growth")
    capacity = required(data, "capacity")
    harvest = required(data, "harvest")
    prices = required(data, "prices")
    costs = required(data, "costs")

    feed_plan_data = required(data, "feed_plan")
    if not isinstance(feed_plan_data, list) or not feed_plan_data:
        raise ValueError("feed_plan must contain at least one row")

    feed_plan = []
    for index, row in enumerate(feed_plan_data):
        if not isinstance(row, dict):
            raise ValueError(f"feed_plan[{index}] must be an object")
        feed_plan.append(
            FeedPlanRow(
                name=str(row.get("name", f"Feed {index + 1}")),
                price_per_kg=positive(row.get("price_per_kg"), f"feed_plan[{index}].price_per_kg"),
                maximum_daily_feed_kg=positive(
                    row.get("maximum_daily_feed_kg"),
                    f"feed_plan[{index}].maximum_daily_feed_kg",
                ),
                use_until_abw_g=positive(row.get("use_until_abw_g"), f"feed_plan[{index}].use_until_abw_g"),
            )
        )

    price_points_data = required(prices, "harvest_price_points")
    if not isinstance(price_points_data, list) or not price_points_data:
        raise ValueError("prices.harvest_price_points must contain at least one row")

    seen_count_sizes = set()
    price_points = []
    for index, row in enumerate(price_points_data):
        if not isinstance(row, dict):
            raise ValueError(f"prices.harvest_price_points[{index}] must be an object")
        count_size = positive(
            row.get("count_size"),
            f"prices.harvest_price_points[{index}].count_size",
        )
        if count_size in seen_count_sizes:
            raise ValueError("prices.harvest_price_points count sizes must be unique")
        seen_count_sizes.add(count_size)
        price_points.append(
            PricePoint(
                count_size=count_size,
                price_per_kg=positive(
                    row.get("price_per_kg"),
                    f"prices.harvest_price_points[{index}].price_per_kg",
                ),
            )
        )

    feeding_index_increment = positive(
        required(growth, "feeding_index_increment"),
        "growth.feeding_index_increment",
    )
    initial_feeding_index = growth.get("initial_feeding_index", 0.55)
    starting_population = integer_positive(
        required(cycle, "starting_population"),
        "cycle.starting_population",
    )
    if "initial_abw_g" in cycle:
        initial_abw_g = positive(cycle["initial_abw_g"], "cycle.initial_abw_g")
    else:
        initial_abw_g = positive(
            required(cycle, "abw_after_blind_feeding_g"),
            "cycle.abw_after_blind_feeding_g",
        )

    mid_cycle_prediction = bool(cycle.get("mid_cycle_prediction", False))
    observed_state_mode = mid_cycle_prediction

    if mid_cycle_prediction:
        if "initial_cumulative_feed_kg" not in cycle:
            raise ValueError("cycle.initial_cumulative_feed_kg is required when mid_cycle_prediction is true")
        if "past_cost" not in cycle:
            raise ValueError("cycle.past_cost is required when mid_cycle_prediction is true")
        total_feed_blind_feeding_kg = 0
        initial_cumulative_feed_kg = non_negative(
            cycle["initial_cumulative_feed_kg"],
            "cycle.initial_cumulative_feed_kg",
        )
        past_cost = non_negative(cycle["past_cost"], "cycle.past_cost")
    elif "total_feed_blind_feeding_kg_per_100k_shrimp" in cycle:
        total_feed_blind_feeding_kg = (
            non_negative(
                cycle["total_feed_blind_feeding_kg_per_100k_shrimp"],
                "cycle.total_feed_blind_feeding_kg_per_100k_shrimp",
            )
            * starting_population
            / 100000
        )
        initial_cumulative_feed_kg = total_feed_blind_feeding_kg
        past_cost = 0
    elif "total_feed_doc_1_to_30_kg_per_100k_shrimp" in cycle:
        total_feed_blind_feeding_kg = (
            non_negative(
                cycle["total_feed_doc_1_to_30_kg_per_100k_shrimp"],
                "cycle.total_feed_doc_1_to_30_kg_per_100k_shrimp",
            )
            * starting_population
            / 100000
        )
        initial_cumulative_feed_kg = total_feed_blind_feeding_kg
        past_cost = 0
    else:
        total_feed_blind_feeding_kg = non_negative(
            required(cycle, "total_feed_doc_1_to_30_kg"),
            "cycle.total_feed_doc_1_to_30_kg",
        )
        initial_cumulative_feed_kg = total_feed_blind_feeding_kg
        past_cost = 0

    blind_feed_additional_cost = non_negative(
        cycle.get("blind_feed_additional_cost", cycle.get("blind_feed_cost_total", 0)),
        "cycle.blind_feed_additional_cost",
    )

    return Config(
        pond_area_m2=positive(required(cycle, "pond_area_m2"), "cycle.pond_area_m2"),
        start_doc=integer_positive(cycle.get("start_doc", 31), "cycle.start_doc"),
        final_doc=integer_positive(required(cycle, "final_doc"), "cycle.final_doc"),
        preparation_day=integer_non_negative(cycle.get("preparation_day", 0), "cycle.preparation_day"),
        starting_population=starting_population,
        initial_abw_g=initial_abw_g,
        maximum_shrimp_size_g=positive(
            required(cycle, "maximum_shrimp_size_g"),
            "cycle.maximum_shrimp_size_g",
        ),
        initial_cumulative_feed_kg=initial_cumulative_feed_kg,
        total_feed_blind_feeding_kg=total_feed_blind_feeding_kg,
        blind_feed_additional_cost=blind_feed_additional_cost,
        past_cost=past_cost,
        observed_state_mode=observed_state_mode,
        target_fcr=positive(required(growth, "target_fcr"), "growth.target_fcr"),
        maximum_adg_g_per_day=positive(
            required(growth, "maximum_adg_g_per_day"),
            "growth.maximum_adg_g_per_day",
        ),
        initial_feeding_index=positive(initial_feeding_index, "growth.initial_feeding_index"),
        feeding_index_increment=feeding_index_increment,
        maximum_feeding_index=positive(
            required(growth, "maximum_feeding_index"),
            "growth.maximum_feeding_index",
        ),
        stable_carrying_capacity_kg_per_m2=positive(
            required(capacity, "stable_carrying_capacity_kg_per_m2"),
            "capacity.stable_carrying_capacity_kg_per_m2",
        ),
        final_carrying_capacity_kg_per_m2=positive(
            required(capacity, "final_carrying_capacity_kg_per_m2"),
            "capacity.final_carrying_capacity_kg_per_m2",
        ),
        minimum_partial_harvest_biomass_kg=positive(
            required(harvest, "minimum_partial_harvest_biomass_kg"),
            "harvest.minimum_partial_harvest_biomass_kg",
        ),
        minimum_partial_harvest_abw_g=non_negative(
            harvest.get("minimum_partial_harvest_abw_g", 0),
            "harvest.minimum_partial_harvest_abw_g",
        ),
        harvest_fixed_cost_per_event=non_negative(
            required(harvest, "harvest_fixed_cost_per_event"),
            "harvest.harvest_fixed_cost_per_event",
        ),
        harvest_price_points=price_points,
        pl_price_per_piece=non_negative(required(costs, "pl_price_per_piece"), "costs.pl_price_per_piece"),
        electricity_kwh=non_negative(
            required(costs, "electricity_kwh"),
            "costs.electricity_kwh",
        ),
        electricity_price_per_kwh=non_negative(
            required(costs, "electricity_price_per_kwh"),
            "costs.electricity_price_per_kwh",
        ),
        labor_cost_per_day=non_negative(required(costs, "labor_cost_per_day"), "costs.labor_cost_per_day"),
        probiotics_cost_per_day=non_negative(
            required(costs, "probiotics_cost_per_day"),
            "costs.probiotics_cost_per_day",
        ),
        disinfection_cost_per_day=non_negative(
            required(costs, "disinfection_cost_per_day"),
            "costs.disinfection_cost_per_day",
        ),
        liming_cost_per_day=non_negative(required(costs, "liming_cost_per_day"), "costs.liming_cost_per_day"),
        feed_plan=feed_plan,
    )


def validate_config(config):
    if config.final_doc < config.start_doc:
        raise ValueError("cycle.final_doc must be greater than or equal to cycle.start_doc")
    if config.maximum_feeding_index < config.initial_feeding_index:
        raise ValueError("growth.maximum_feeding_index must be greater than or equal to growth.initial_feeding_index")


def selected_feed_for_abw(feed_plan, abw_g):
    for row in feed_plan:
        if abw_g <= row.use_until_abw_g:
            return row
    return feed_plan[-1]


def interpolate_price(price_points, count_size):
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
    daily_results: tuple
    partial_harvests: tuple


def feeding_index_for_doc(config, doc):
    elapsed_days = doc - config.start_doc
    return min(
        config.maximum_feeding_index,
        config.initial_feeding_index + config.feeding_index_increment * elapsed_days,
    )


def stable_capacity_kg(config):
    return config.pond_area_m2 * config.stable_carrying_capacity_kg_per_m2


def final_capacity_kg(config):
    return config.pond_area_m2 * config.final_carrying_capacity_kg_per_m2


def initial_feed_cost(config):
    if config.observed_state_mode:
        return 0
    blind_feed_feed_cost = config.total_feed_blind_feeding_kg * config.feed_plan[0].price_per_kg
    return blind_feed_feed_cost + config.blind_feed_additional_cost


def daily_cost_totals(config, production_day_count):
    if config.observed_state_mode:
        preparation_days = 0
        blind_feeding_days = 0
    else:
        preparation_days = config.preparation_day
        blind_feeding_days = max(0, config.start_doc - 1)
    operating_days = preparation_days + blind_feeding_days + production_day_count
    light_daily_cost = (
        config.labor_cost_per_day
        + config.probiotics_cost_per_day
        + config.liming_cost_per_day
    )
    preparation_cost = light_daily_cost * preparation_days
    electricity_daily_cost = config.electricity_kwh * 24 * config.electricity_price_per_kwh
    blind_feeding_daily_costs = (light_daily_cost + electricity_daily_cost) * blind_feeding_days
    labor_cost = config.labor_cost_per_day * operating_days
    electricity_cost = electricity_daily_cost * (blind_feeding_days + production_day_count)
    probiotics_cost = config.probiotics_cost_per_day * operating_days
    disinfection_cost = config.disinfection_cost_per_day * production_day_count
    liming_cost = config.liming_cost_per_day * operating_days
    production_daily_operating_cost = (
        electricity_daily_cost
        + config.labor_cost_per_day
        + config.probiotics_cost_per_day
        + config.disinfection_cost_per_day
        + config.liming_cost_per_day
    )
    production_daily_costs = production_daily_operating_cost * production_day_count
    return (
        preparation_cost,
        blind_feeding_daily_costs,
        production_daily_costs,
        labor_cost,
        electricity_cost,
        probiotics_cost,
        disinfection_cost,
        liming_cost,
        preparation_cost + blind_feeding_daily_costs + production_daily_costs,
    )


def run_daily_step(
    config,
    doc,
    population,
    biomass_kg,
    cumulative_feed_kg,
    simulated_feed_kg,
    feed_cost,
    stable_locked,
    day_start_population=None,
    day_start_biomass_kg=None,
    partial_event=None,
    suppress_feed=False,
    forced_stop_reason=None,
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
    if suppress_feed:
        actual_feed_kg = 0

    biomass_gain_kg = actual_feed_kg / config.target_fcr
    biomass_kg += biomass_gain_kg
    ending_abw_g = biomass_kg * 1000 / population
    adg_g_per_day = ending_abw_g - starting_abw_g
    count_size = 1000 / ending_abw_g
    harvest_price_per_kg = interpolate_price(config.harvest_price_points, count_size)
    cumulative_feed_kg += actual_feed_kg
    simulated_feed_kg += actual_feed_kg
    daily_feed_cost = actual_feed_kg * feed.price_per_kg
    feed_cost += daily_feed_cost
    stable_locked_after_day = stable_locked or biomass_kg >= stable_capacity_kg(config)

    if forced_stop_reason is not None:
        day_stop_reason = forced_stop_reason
    else:
        day_stop_reason = ""
        if biomass_kg >= final_capacity_kg(config):
            day_stop_reason = "final_carrying_capacity"
        elif ending_abw_g >= config.maximum_shrimp_size_g:
            day_stop_reason = "maximum_shrimp_size"
        elif doc == config.final_doc:
            day_stop_reason = "final_doc"

    row = DailyResult(
        doc=doc,
        starting_population=day_start_population,
        ending_population=population,
        feed_name=feed.name,
        feeding_index=feeding_index,
        starting_abw_g=starting_abw_g,
        ending_abw_g=ending_abw_g,
        starting_biomass_kg=day_start_biomass_kg,
        ending_biomass_kg=biomass_kg,
        feed_from_index_kg=feed_from_index,
        feed_from_type_limit_kg=feed_from_type_limit,
        feed_from_adg_limit_kg=feed_from_adg_limit,
        feed_from_size_limit_kg=feed_from_size_limit,
        actual_feed_kg=actual_feed_kg,
        biomass_gain_kg=biomass_gain_kg,
        adg_g_per_day=adg_g_per_day,
        cumulative_feed_kg=cumulative_feed_kg,
        count_size=count_size,
        harvest_price_per_kg=harvest_price_per_kg,
        partial_harvest_kg=partial_event.kg_harvested if partial_event else 0,
        partial_harvest_revenue=partial_event.revenue if partial_event else 0,
        partial_harvest_price_per_kg=partial_event.price_per_kg if partial_event else 0,
        partial_harvest_population_removed=partial_event.population_removed if partial_event else 0,
        stable_locked=stable_locked_after_day,
        feed_cost=daily_feed_cost,
        stop_reason=day_stop_reason,
    )
    return (
        population,
        biomass_kg,
        cumulative_feed_kg,
        simulated_feed_kg,
        feed_cost,
        row,
        day_stop_reason,
        stable_locked_after_day,
    )


def finalize_simulation(
    config,
    final_doc,
    population,
    biomass_kg,
    cumulative_feed_kg,
    simulated_feed_kg,
    feed_cost,
    partial_revenue,
    partial_harvest_cost,
    partial_harvests,
    daily_results,
    stop_reason,
):
    final_abw_g = biomass_kg * 1000 / population
    harvest_count_size = 1000 / final_abw_g
    harvest_price_per_kg = interpolate_price(config.harvest_price_points, harvest_count_size)
    final_revenue = biomass_kg * harvest_price_per_kg
    total_revenue = partial_revenue + final_revenue
    past_cost = config.past_cost
    pl_cost = 0 if config.observed_state_mode else config.starting_population * config.pl_price_per_piece
    (
        preparation_cost,
        blind_feeding_daily_costs,
        production_daily_costs,
        labor_cost,
        electricity_cost,
        probiotics_cost,
        disinfection_cost,
        liming_cost,
        daily_costs,
    ) = daily_cost_totals(config, len(daily_results))
    total_harvest_event_cost = partial_harvest_cost + config.harvest_fixed_cost_per_event
    total_costs = past_cost + pl_cost + feed_cost + daily_costs + total_harvest_event_cost
    profit = total_revenue - total_costs
    profit_per_day = profit / (config.preparation_day + final_doc)

    return SimulationResult(
        final_doc=final_doc,
        final_abw_g=final_abw_g,
        final_biomass_kg=biomass_kg,
        cumulative_feed_kg=cumulative_feed_kg,
        simulated_feed_kg=simulated_feed_kg,
        final_revenue=final_revenue,
        partial_revenue=partial_revenue,
        total_revenue=total_revenue,
        past_cost=past_cost,
        observed_state_mode=config.observed_state_mode,
        pl_cost=pl_cost,
        feed_cost=feed_cost,
        preparation_cost=preparation_cost,
        blind_feeding_daily_costs=blind_feeding_daily_costs,
        production_daily_costs=production_daily_costs,
        labor_cost=labor_cost,
        electricity_cost=electricity_cost,
        probiotics_cost=probiotics_cost,
        disinfection_cost=disinfection_cost,
        liming_cost=liming_cost,
        daily_costs=daily_costs,
        harvest_cost=total_harvest_event_cost,
        total_harvest_event_cost=total_harvest_event_cost,
        total_costs=total_costs,
        profit=profit,
        profit_per_day=profit_per_day,
        harvest_count_size=harvest_count_size,
        harvest_price_per_kg=harvest_price_per_kg,
        stop_reason=stop_reason,
        partial_harvests=list(partial_harvests),
        daily_results=list(daily_results),
    )


def generate_partial_harvest_candidates(config, biomass_kg):
    first_candidate = int(
        math.ceil(config.minimum_partial_harvest_biomass_kg / PARTIAL_HARVEST_STEP_KG)
        * PARTIAL_HARVEST_STEP_KG
    )
    max_candidate = int(math.floor((biomass_kg - 1e-9) / PARTIAL_HARVEST_STEP_KG) * PARTIAL_HARVEST_STEP_KG)
    if max_candidate < first_candidate:
        return []
    return list(range(first_candidate, max_candidate + PARTIAL_HARVEST_STEP_KG, PARTIAL_HARVEST_STEP_KG))


def apply_partial_harvest(config, doc, population, biomass_kg, harvest_kg):
    abw_g = biomass_kg * 1000 / population
    if abw_g < config.minimum_partial_harvest_abw_g:
        return None
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


def current_liquidation_value_from_values(config, population, biomass_kg, partial_revenue, feed_cost, partial_harvest_cost):
    abw_g = biomass_kg * 1000 / population
    count_size = 1000 / abw_g
    standing_revenue = biomass_kg * interpolate_price(config.harvest_price_points, count_size)
    return partial_revenue + standing_revenue - feed_cost - partial_harvest_cost


def current_liquidation_value(config, state):
    return current_liquidation_value_from_values(
        config,
        state.population,
        state.biomass_kg,
        state.partial_revenue,
        state.feed_cost,
        state.partial_harvest_cost,
    )


def optimizer_bucket_key_from_values(doc, stable_locked, population, biomass_kg):
    rounded_population = int(round(population / 250) * 250)
    rounded_biomass = int(round(biomass_kg / 5) * 5)
    return (doc, stable_locked, rounded_population, rounded_biomass)


def optimizer_bucket_key(state):
    return optimizer_bucket_key_from_values(state.doc, state.stable_locked, state.population, state.biomass_kg)


def optimizer_state_rank_from_values(
    config,
    population,
    biomass_kg,
    partial_revenue,
    feed_cost,
    partial_harvest_cost,
    partial_harvest_count,
):
    return (
        current_liquidation_value_from_values(
            config,
            population,
            biomass_kg,
            partial_revenue,
            feed_cost,
            partial_harvest_cost,
        ),
        -partial_harvest_count,
        biomass_kg,
        population,
    )


def optimizer_state_rank(config, state):
    return optimizer_state_rank_from_values(
        config,
        state.population,
        state.biomass_kg,
        state.partial_revenue,
        state.feed_cost,
        state.partial_harvest_cost,
        len(state.partial_harvests),
    )


def prune_optimizer_states(config, states):
    best_by_bucket = {}
    best_rank_by_bucket = {}
    for state in states:
        key = optimizer_bucket_key(state)
        rank = optimizer_state_rank(config, state)
        if key not in best_by_bucket or rank > best_rank_by_bucket[key]:
            best_by_bucket[key] = state
            best_rank_by_bucket[key] = rank

    pruned = list(best_by_bucket.values())
    return limit_optimizer_states(config, pruned)


def limit_optimizer_states(config, states):
    if len(states) > MAX_OPTIMIZER_STATES_PER_DOC:
        states.sort(
            key=lambda state: optimizer_state_rank(config, state),
            reverse=True,
        )
        states = states[:MAX_OPTIMIZER_STATES_PER_DOC]
    return states


def result_rank(result):
    return (
        result.profit_per_day,
        result.profit,
        result.final_abw_g,
        -len(result.partial_harvests),
    )


def better_result(candidate, current):
    if current is None:
        return True
    return result_rank(candidate) > result_rank(current)


def simulate(config):
    validate_config(config)

    population = config.starting_population
    biomass_kg = population * config.initial_abw_g / 1000
    cumulative_feed_kg = config.initial_cumulative_feed_kg
    simulated_feed_kg = 0
    feed_cost = initial_feed_cost(config)
    stable_locked = biomass_kg >= stable_capacity_kg(config)
    daily_results = []
    stop_reason = "final_doc"
    final_doc = config.start_doc - 1

    for doc in range(config.start_doc, config.final_doc + 1):
        day_start_population = population
        day_start_biomass_kg = biomass_kg
        day_start_cumulative_feed_kg = cumulative_feed_kg
        day_start_simulated_feed_kg = simulated_feed_kg
        day_start_feed_cost = feed_cost
        day_start_stable_locked = stable_locked
        (
            population,
            biomass_kg,
            cumulative_feed_kg,
            simulated_feed_kg,
            feed_cost,
            daily_result,
            day_stop_reason,
            stable_locked,
        ) = run_daily_step(
            config,
            doc,
            population,
            biomass_kg,
            cumulative_feed_kg,
            simulated_feed_kg,
            feed_cost,
            stable_locked,
        )
        if day_stop_reason:
            (
                population,
                biomass_kg,
                cumulative_feed_kg,
                simulated_feed_kg,
                feed_cost,
                daily_result,
                day_stop_reason,
                stable_locked,
            ) = run_daily_step(
                config,
                doc,
                day_start_population,
                day_start_biomass_kg,
                day_start_cumulative_feed_kg,
                day_start_simulated_feed_kg,
                day_start_feed_cost,
                day_start_stable_locked,
                suppress_feed=True,
                forced_stop_reason=day_stop_reason,
            )
        daily_results.append(daily_result)
        final_doc = doc

        if day_stop_reason:
            stop_reason = day_stop_reason
            if day_stop_reason != "final_doc":
                break

    return finalize_simulation(
        config=config,
        final_doc=final_doc,
        population=population,
        biomass_kg=biomass_kg,
        cumulative_feed_kg=cumulative_feed_kg,
        simulated_feed_kg=simulated_feed_kg,
        feed_cost=feed_cost,
        partial_revenue=0,
        partial_harvest_cost=0,
        partial_harvests=[],
        daily_results=daily_results,
        stop_reason=stop_reason,
    )


def optimize_partial_harvests(config):
    validate_config(config)

    population = config.starting_population
    biomass_kg = population * config.initial_abw_g / 1000
    initial_state = OptimizerState(
        doc=config.start_doc,
        population=population,
        biomass_kg=biomass_kg,
        cumulative_feed_kg=config.initial_cumulative_feed_kg,
        simulated_feed_kg=0,
        feed_cost=initial_feed_cost(config),
        partial_revenue=0,
        partial_harvest_cost=0,
        stable_locked=biomass_kg >= stable_capacity_kg(config),
        daily_results=(),
        partial_harvests=(),
    )

    states = [initial_state]
    best = None
    stable_capacity_limit_kg = stable_capacity_kg(config)

    for doc in range(config.start_doc, config.final_doc + 1):
        next_best_by_bucket = {}
        next_rank_by_bucket = {}
        for state in states:
            state_stable_locked = state.stable_locked or state.biomass_kg >= stable_capacity_limit_kg
            harvest_choices = [None]
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
                    applied = apply_partial_harvest(
                        config,
                        doc,
                        state.population,
                        state.biomass_kg,
                        harvest_kg,
                    )
                    if applied is None:
                        continue
                    population_after_harvest, biomass_after_harvest_kg, partial_event = applied
                    partial_revenue += partial_event.revenue
                    partial_harvest_cost += partial_event.fixed_cost
                    partial_harvests = state.partial_harvests + (partial_event,)

                (
                    next_population,
                    next_biomass_kg,
                    next_cumulative_feed_kg,
                    next_simulated_feed_kg,
                    next_feed_cost,
                    daily_result,
                    day_stop_reason,
                    next_stable_locked,
                ) = run_daily_step(
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
                    (
                        next_population,
                        next_biomass_kg,
                        next_cumulative_feed_kg,
                        next_simulated_feed_kg,
                        next_feed_cost,
                        daily_result,
                        day_stop_reason,
                        next_stable_locked,
                    ) = run_daily_step(
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
                        suppress_feed=True,
                        forced_stop_reason=day_stop_reason,
                    )
                    daily_results = state.daily_results + (daily_result,)
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
                        daily_results=daily_results,
                        stop_reason=day_stop_reason,
                    )
                    if better_result(candidate, best):
                        best = candidate
                else:
                    key = optimizer_bucket_key_from_values(
                        doc + 1,
                        next_stable_locked,
                        next_population,
                        next_biomass_kg,
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
                        daily_results = state.daily_results + (daily_result,)
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
                            daily_results=daily_results,
                            partial_harvests=partial_harvests,
                        )
                        next_rank_by_bucket[key] = rank

        if doc == config.final_doc:
            break
        states = limit_optimizer_states(config, list(next_best_by_bucket.values()))
        if not states:
            break

    if best is None:
        return simulate(config)
    return best


def read_input(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def write_daily_csv(path, daily_results):
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(dataclasses.asdict(daily_results[0]).keys()))
        writer.writeheader()
        for row in daily_results:
            writer.writerow(dataclasses.asdict(row))


def escape_html(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def chart_number(value):
    absolute = abs(value)
    if absolute >= 1000:
        return f"{value:,.0f}"
    if absolute >= 100:
        return f"{value:,.1f}"
    if absolute >= 10:
        return f"{value:,.2f}"
    return f"{value:,.3f}"


def chart_points(x_values, y_values, min_y, max_y, width, height, pad_left, pad_top, pad_right, pad_bottom):
    if len(x_values) == 1:
        x_range = 1
    else:
        x_range = x_values[-1] - x_values[0]
    y_range = max_y - min_y
    if y_range == 0:
        y_range = 1

    plot_width = width - pad_left - pad_right
    plot_height = height - pad_top - pad_bottom
    points = []
    for x_value, y_value in zip(x_values, y_values):
        x_position = pad_left + ((x_value - x_values[0]) / x_range) * plot_width
        y_position = pad_top + (1 - ((y_value - min_y) / y_range)) * plot_height
        points.append(f"{x_position:.2f},{y_position:.2f}")
    return " ".join(points)


def render_chart(title, unit, x_values, series, min_y=None, x_label="DOC", markers=None):
    markers = markers or []
    width = 820
    height = 300
    pad_left = 72
    pad_top = 34
    pad_right = 24
    pad_bottom = 42
    all_values = [value for item in series for value in item["values"]]
    all_values.extend(marker["y"] for marker in markers)
    chart_min_y = min(all_values) if min_y is None else min_y
    chart_max_y = max(all_values)
    if chart_max_y == chart_min_y:
        chart_max_y = chart_min_y + 1
    else:
        padding = (chart_max_y - chart_min_y) * 0.08
        chart_max_y += padding
        if min_y is None:
            chart_min_y -= padding

    x_min = x_values[0]
    x_max = x_values[-1]
    y_ticks = [
        chart_min_y,
        chart_min_y + (chart_max_y - chart_min_y) / 2,
        chart_max_y,
    ]
    x_ticks = [x_min, x_min + (x_max - x_min) / 2, x_max]
    plot_width = width - pad_left - pad_right
    plot_height = height - pad_top - pad_bottom

    elements = [
        f'<article class="chart-card">',
        f"<h2>{escape_html(title)}</h2>",
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape_html(title)}">',
        f'<rect class="plot-bg" x="{pad_left}" y="{pad_top}" width="{plot_width}" height="{plot_height}"></rect>',
    ]

    for tick in y_ticks:
        y = pad_top + (1 - ((tick - chart_min_y) / (chart_max_y - chart_min_y))) * plot_height
        elements.append(f'<line class="grid" x1="{pad_left}" y1="{y:.2f}" x2="{width - pad_right}" y2="{y:.2f}"></line>')
        elements.append(f'<text class="axis-label" x="{pad_left - 10}" y="{y + 4:.2f}" text-anchor="end">{chart_number(tick)}</text>')

    for tick in x_ticks:
        x = pad_left if x_max == x_min else pad_left + ((tick - x_min) / (x_max - x_min)) * plot_width
        elements.append(
            f'<text class="axis-label" x="{x:.2f}" y="{height - 12}" text-anchor="middle">'
            f'{escape_html(x_label)} {chart_number(tick)}</text>'
        )

    for item in series:
        points = chart_points(
            x_values,
            item["values"],
            chart_min_y,
            chart_max_y,
            width,
            height,
            pad_left,
            pad_top,
            pad_right,
            pad_bottom,
        )
        dash = ' stroke-dasharray="8 6"' if item.get("dash") else ""
        elements.append(
            f'<polyline points="{points}" fill="none" stroke="{item["color"]}" stroke-width="2.5" '
            f'stroke-linejoin="round" stroke-linecap="round"{dash}></polyline>'
        )

    for marker in markers:
        x = pad_left if x_max == x_min else pad_left + ((marker["x"] - x_min) / (x_max - x_min)) * plot_width
        y = pad_top + (1 - ((marker["y"] - chart_min_y) / (chart_max_y - chart_min_y))) * plot_height
        elements.append(
            f'<circle class="harvest-marker" cx="{x:.2f}" cy="{y:.2f}" r="5">'
            f'<title>{escape_html(marker["label"])}</title></circle>'
        )

    elements.append(f'<text class="unit-label" x="{pad_left}" y="22">{escape_html(unit)}</text>')
    elements.append("</svg>")
    elements.append('<div class="legend">')
    for item in series:
        dash_class = " dashed" if item.get("dash") else ""
        latest = item["values"][-1]
        elements.append(
            f'<span><i class="swatch{dash_class}" style="color:{item["color"]}"></i>'
            f'{escape_html(item["label"])} <strong>{chart_number(latest)}</strong></span>'
        )
    if markers:
        elements.append('<span><i class="harvest-dot"></i>Partial harvest</span>')
    elements.append("</div>")
    elements.append("</article>")
    return "\n".join(elements)


def partial_harvest_markers(result, value_for_row):
    markers = []
    for row in result.daily_results:
        if row.partial_harvest_kg > 0:
            markers.append(
                {
                    "x": row.doc,
                    "y": value_for_row(row),
                    "label": (
                        f"DOC {row.doc}: partial harvest {row.partial_harvest_kg:,.0f} kg, "
                        f"revenue {row.partial_harvest_revenue:,.0f}"
                    ),
                }
            )
    return markers


def total_harvested_biomass_kg(result):
    return result.final_biomass_kg + sum(event.kg_harvested for event in result.partial_harvests)


def write_charts_html(path, config, result):
    if not result.daily_results:
        raise ValueError("simulation produced no daily rows")

    docs = [row.doc for row in result.daily_results]
    price_count_sizes = list(range(100, 19, -1))
    stable_threshold_kg = config.pond_area_m2 * config.stable_carrying_capacity_kg_per_m2
    capacity_threshold_kg = config.pond_area_m2 * config.final_carrying_capacity_kg_per_m2
    charts = [
        render_chart(
            "Daily Feed",
            "kg/day",
            docs,
            [
                {
                    "label": "Daily feed",
                    "values": [row.actual_feed_kg for row in result.daily_results],
                    "color": "#2563eb",
                },
                {
                    "label": "Max daily feed",
                    "values": [row.feed_from_type_limit_kg for row in result.daily_results],
                    "color": "#dc2626",
                    "dash": True,
                },
            ],
            min_y=0,
            markers=partial_harvest_markers(result, lambda row: row.actual_feed_kg),
        ),
        render_chart(
            "Cumulative Feed",
            "kg",
            docs,
            [
                {
                    "label": "Cumulative feed",
                    "values": [row.cumulative_feed_kg for row in result.daily_results],
                    "color": "#0f766e",
                },
            ],
            min_y=0,
            markers=partial_harvest_markers(result, lambda row: row.cumulative_feed_kg),
        ),
        render_chart(
            "Feeding Index",
            "index",
            docs,
            [
                {
                    "label": "Feeding index",
                    "values": [row.feeding_index for row in result.daily_results],
                    "color": "#7c3aed",
                },
                {
                    "label": "Max feeding index",
                    "values": [config.maximum_feeding_index for _ in result.daily_results],
                    "color": "#a16207",
                    "dash": True,
                },
            ],
            min_y=0,
        ),
        render_chart(
            "ADG",
            "g/day",
            docs,
            [
                {
                    "label": "ADG",
                    "values": [row.adg_g_per_day for row in result.daily_results],
                    "color": "#ea580c",
                },
                {
                    "label": "Max ADG",
                    "values": [config.maximum_adg_g_per_day for _ in result.daily_results],
                    "color": "#dc2626",
                    "dash": True,
                },
            ],
            min_y=0,
        ),
        render_chart(
            "ABW",
            "g",
            docs,
            [
                {
                    "label": "ABW",
                    "values": [row.ending_abw_g for row in result.daily_results],
                    "color": "#0891b2",
                },
            ],
            min_y=0,
            markers=partial_harvest_markers(result, lambda row: row.ending_abw_g),
        ),
        render_chart(
            "Biomass",
            "kg",
            docs,
            [
                {
                    "label": "Biomass",
                    "values": [row.ending_biomass_kg for row in result.daily_results],
                    "color": "#16a34a",
                },
                {
                    "label": "Stable capacity",
                    "values": [stable_threshold_kg for _ in result.daily_results],
                    "color": "#a16207",
                    "dash": True,
                },
                {
                    "label": "Final capacity",
                    "values": [capacity_threshold_kg for _ in result.daily_results],
                    "color": "#dc2626",
                    "dash": True,
                },
            ],
            min_y=0,
            markers=partial_harvest_markers(result, lambda row: row.ending_biomass_kg),
        ),
        render_chart(
            "Estimated Harvest Price",
            "price/kg",
            price_count_sizes,
            [
                {
                    "label": "Price",
                    "values": [
                        interpolate_price(config.harvest_price_points, count_size)
                        for count_size in price_count_sizes
                    ],
                    "color": "#be123c",
                },
            ],
            x_label="Count",
        ),
        render_chart(
            "Count Size",
            "shrimp/kg",
            docs,
            [
                {
                    "label": "Count size",
                    "values": [row.count_size for row in result.daily_results],
                    "color": "#475569",
                },
            ],
            min_y=0,
        ),
    ]

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Shrimp Prediction Charts</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Arial, Helvetica, sans-serif;
      background: #f6f7f9;
      color: #172033;
    }}
    body {{
      margin: 0;
      padding: 28px;
    }}
    header {{
      max-width: 1120px;
      margin: 0 auto 18px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      font-weight: 700;
    }}
    .summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      color: #475569;
      font-size: 14px;
    }}
    .summary span {{
      background: #ffffff;
      border: 1px solid #dbe3ef;
      border-radius: 6px;
      padding: 6px 9px;
    }}
    main {{
      max-width: 1120px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
      gap: 16px;
    }}
    .chart-card {{
      background: #ffffff;
      border: 1px solid #dbe3ef;
      border-radius: 8px;
      padding: 14px 14px 12px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
    }}
    .chart-card h2 {{
      margin: 0 0 8px;
      font-size: 16px;
      font-weight: 700;
    }}
    svg {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .plot-bg {{
      fill: #fbfcfe;
      stroke: #dbe3ef;
    }}
    .grid {{
      stroke: #e5eaf1;
      stroke-width: 1;
    }}
    .axis-label, .unit-label {{
      fill: #64748b;
      font-size: 12px;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 14px;
      margin-top: 8px;
      color: #475569;
      font-size: 13px;
    }}
    .legend span {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .legend strong {{
      color: #172033;
      font-weight: 700;
    }}
    .swatch {{
      display: inline-block;
      width: 18px;
      height: 3px;
      border-radius: 999px;
      background: currentColor;
    }}
    .swatch.dashed {{
      background-image: repeating-linear-gradient(
        90deg,
        currentColor 0,
        currentColor 7px,
        transparent 7px,
        transparent 11px
      );
    }}
    .harvest-marker {{
      fill: #f59e0b;
      stroke: #78350f;
      stroke-width: 1.5;
    }}
    .harvest-dot {{
      display: inline-block;
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: #f59e0b;
      border: 1px solid #78350f;
    }}
    @media (max-width: 560px) {{
      body {{
        padding: 14px;
      }}
      main {{
        grid-template-columns: 1fr;
      }}
      .chart-card {{
        padding: 10px;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Shrimp Prediction Charts</h1>
    <div class="summary">
      <span>Final DOC {result.final_doc}</span>
      <span>Stop: {escape_html(result.stop_reason)}</span>
      <span>Final ABW {result.final_abw_g:,.2f} g</span>
      <span>Biomass {result.final_biomass_kg:,.2f} kg</span>
      <span>Total biomass {total_harvested_biomass_kg(result):,.2f} kg</span>
      <span>Profit {result.profit:,.0f}</span>
      <span>Profit/day {result.profit_per_day:,.0f}</span>
      <span>Partial harvests {len(result.partial_harvests)}</span>
    </div>
  </header>
  <main>
    {"".join(charts)}
  </main>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8", newline="") as file:
        file.write(html)


def format_money(value):
    return f"{value:,.0f}"


def format_kg(value):
    return f"{value:,.2f}"


def print_summary(result):
    print(f"Best profit/day: {format_money(result.profit_per_day)}")
    print(f"Final DOC: {result.final_doc}")
    print(f"Stop reason: {result.stop_reason}")
    print(f"Final ABW: {result.final_abw_g:,.2f} g")
    print(f"Biomass: {format_kg(result.final_biomass_kg)} kg")
    print(f"Total biomass incl. partial harvests: {format_kg(total_harvested_biomass_kg(result))} kg")
    print(f"Feed used: {format_kg(result.cumulative_feed_kg)} kg")
    print(f"Harvest count size: {result.harvest_count_size:,.2f}")
    print(f"Harvest price: {format_money(result.harvest_price_per_kg)} / kg")
    print(f"Partial harvest count: {len(result.partial_harvests)}")
    print(f"Partial revenue: {format_money(result.partial_revenue)}")
    print(f"Final revenue: {format_money(result.final_revenue)}")
    print(f"Total revenue: {format_money(result.total_revenue)}")
    print("Cost breakdown:")
    cost_names = {
        "feed": "Future feed cost" if result.observed_state_mode else "Feed cost",
        "labor": "Future labor cost" if result.observed_state_mode else "Labor cost",
        "electricity": "Future electricity cost" if result.observed_state_mode else "Electricity cost",
        "probiotics": "Future probiotics cost" if result.observed_state_mode else "Probiotics cost",
        "disinfection": "Future disinfection cost" if result.observed_state_mode else "Disinfection cost",
        "liming": "Future liming cost" if result.observed_state_mode else "Liming cost",
    }
    print(f"  Past cost: {format_money(result.past_cost)}")
    print(f"  Seed cost: {format_money(result.pl_cost)}")
    print(f"  {cost_names['feed']}: {format_money(result.feed_cost)}")
    print(f"  {cost_names['labor']}: {format_money(result.labor_cost)}")
    print(f"  {cost_names['electricity']}: {format_money(result.electricity_cost)}")
    print(f"  {cost_names['probiotics']}: {format_money(result.probiotics_cost)}")
    print(f"  {cost_names['disinfection']}: {format_money(result.disinfection_cost)}")
    print(f"  {cost_names['liming']}: {format_money(result.liming_cost)}")
    print(f"  Harvest event costs: {format_money(result.total_harvest_event_cost)}")
    print(f"Total costs: {format_money(result.total_costs)}")
    print(f"Total profit: {format_money(result.profit)}")
    print("Partial harvest schedule:")
    if not result.partial_harvests:
        print("  none")
        return

    print("  DOC | kg harvested | ABW g | count size | price/kg | revenue")
    for event in result.partial_harvests:
        print(
            "  "
            f"{event.doc:>3} | "
            f"{event.kg_harvested:>12,.0f} | "
            f"{event.abw_g:>5,.2f} | "
            f"{event.count_size:>10,.2f} | "
            f"{format_money(event.price_per_kg):>8} | "
            f"{format_money(event.revenue):>8}"
        )


def main():
    parser = argparse.ArgumentParser(description="Run a deterministic shrimp growth/feed simulation.")
    parser.add_argument("input_json", help="Path to prediction-input.json")
    parser.add_argument("--daily-csv", help="Optional path for one-row-per-DOC CSV output")
    parser.add_argument("--charts-html", help="Optional path for a self-contained HTML chart report")
    parser.add_argument(
        "--optimize-partial-harvests",
        action="store_true",
        help="Search for the best partial-harvest schedule by net profit per whole cycle day",
    )
    args = parser.parse_args()

    try:
        config = load_config(read_input(args.input_json))
        if args.optimize_partial_harvests:
            result = optimize_partial_harvests(config)
        else:
            result = simulate(config)
        if args.daily_csv:
            if not result.daily_results:
                raise ValueError("simulation produced no daily rows")
            write_daily_csv(args.daily_csv, result.daily_results)
        if args.charts_html:
            write_charts_html(args.charts_html, config, result)
        print_summary(result)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise SystemExit(f"error: {error}")


if __name__ == "__main__":
    main()
