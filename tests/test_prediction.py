from app.services import prediction


def base_config(**overrides):
    values = {
        "pond_area_m2": 1000,
        "start_doc": 31,
        "final_doc": 32,
        "preparation_day": 0,
        "starting_population": 100000,
        "initial_abw_g": 5,
        "maximum_shrimp_size_g": 100,
        "initial_cumulative_feed_kg": 0,
        "past_cost": 0,
        "target_fcr": 1,
        "maximum_adg_g_per_day": 10,
        "initial_feeding_index": 1,
        "feeding_index_increment": 0.01,
        "maximum_feeding_index": 2,
        "stable_carrying_capacity_kg_per_m2": 2,
        "final_carrying_capacity_kg_per_m2": 100,
        "minimum_partial_harvest_biomass_kg": 350,
        "harvest_fixed_cost_per_event": 0,
        "harvest_price_points": [
            prediction.PricePoint(count_size=20, price_per_kg=80),
            prediction.PricePoint(count_size=40, price_per_kg=60),
        ],
        "pl_price_per_piece": 0,
        "electricity_kwh": 0,
        "electricity_price_per_kwh": 0,
        "labor_cost_per_day": 0,
        "probiotics_cost_per_day": 0,
        "disinfection_cost_per_day": 0,
        "liming_cost_per_day": 0,
        "feed_plan": [
            prediction.FeedPlanRow(
                feed_type_id="feed-a",
                name="Test Feed",
                brand="Brand",
                type="Starter",
                price_per_kg=1,
                maximum_daily_feed_kg=1000,
                use_until_abw_g=999,
            )
        ],
    }
    values.update(overrides)
    return prediction.Config(**values)


def profitable_partial_config():
    return base_config(
        final_doc=60,
        stable_carrying_capacity_kg_per_m2=0.7,
        final_carrying_capacity_kg_per_m2=0.8,
        harvest_price_points=[
            prediction.PricePoint(count_size=20, price_per_kg=1000),
            prediction.PricePoint(count_size=40, price_per_kg=500),
            prediction.PricePoint(count_size=200, price_per_kg=1),
        ],
    )


def test_price_interpolation_and_clamping():
    points = [
        prediction.PricePoint(count_size=40, price_per_kg=60),
        prediction.PricePoint(count_size=20, price_per_kg=80),
    ]

    assert prediction.interpolate_price(points, 10) == 80
    assert prediction.interpolate_price(points, 50) == 60
    assert prediction.interpolate_price(points, 30) == 70


def test_same_day_prediction_has_no_feedings():
    result = prediction.simulate(base_config(final_doc=31))

    assert result.final_doc == 31
    assert result.daily_results[0].actual_feed_kg == 0
    assert result.final_abw_g == 5


def test_feed_cap_by_max_daily_feed():
    feed = base_config().feed_plan[0]
    feed.maximum_daily_feed_kg = 10

    result = prediction.simulate(base_config(feed_plan=[feed]))

    assert result.daily_results[0].actual_feed_kg == 10


def test_feed_cap_by_max_adg():
    result = prediction.simulate(base_config(target_fcr=2, maximum_adg_g_per_day=0.1))

    assert result.daily_results[0].actual_feed_kg == 20


def test_early_stop_at_final_carrying_capacity():
    result = prediction.simulate(
        base_config(
            starting_population=1000,
            initial_abw_g=5,
            final_doc=120,
            final_carrying_capacity_kg_per_m2=0.0052,
        )
    )

    assert result.stop_reason == "final_carrying_capacity"
    assert result.final_doc == 31


def test_early_stop_at_maximum_shrimp_size():
    result = prediction.simulate(
        base_config(
            starting_population=1000,
            initial_abw_g=9,
            maximum_shrimp_size_g=10,
            final_doc=120,
            initial_feeding_index=4,
            maximum_feeding_index=4,
        )
    )

    assert result.stop_reason == "maximum_shrimp_size"
    assert result.final_doc == 31


def test_optimizer_chooses_profitable_partial_harvest():
    baseline = prediction.simulate(profitable_partial_config())
    optimized = prediction.optimize_partial_harvests(profitable_partial_config())

    assert len(optimized.partial_harvests) >= 1
    assert optimized.profit_per_day > baseline.profit_per_day
