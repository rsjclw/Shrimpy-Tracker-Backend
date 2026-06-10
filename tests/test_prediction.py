from datetime import date, time
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import DailyLog
from app.schemas.prediction import (
    PredictionDailyRowOut,
    PredictionPartialHarvestOut,
    PredictionResultOut,
    PredictionSummaryOut,
)
from app.services import prediction
from app.services import prediction_core as core


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
        "total_feed_blind_feeding_kg": 0.0,
        "blind_feed_additional_cost": 0.0,
        "past_cost": 0,
        "observed_state_mode": True,
        "target_fcr": 1,
        "maximum_adg_g_per_day": 10,
        "initial_feeding_index": 1,
        "feeding_index_increment": 0.01,
        "maximum_feeding_index": 2,
        "stable_carrying_capacity_kg_per_m2": 2,
        "final_carrying_capacity_kg_per_m2": 100,
        "minimum_partial_harvest_biomass_kg": 350,
        "minimum_partial_harvest_abw_g": 0,
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
    return core.Config(**values)


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

    assert core.interpolate_price(points, 10) == 80
    assert core.interpolate_price(points, 50) == 60
    assert core.interpolate_price(points, 30) == 70


def test_single_day_prediction_feeds_start_day():
    # Source-of-truth core feeds on every simulated day, including the start/terminal
    # day (the old backend fork left the terminal day unfed).
    result = core.simulate(base_config(final_doc=31))

    assert result.final_doc == 31
    assert result.daily_results[0].actual_feed_kg == 31
    assert result.final_abw_g == 5.31


def test_feed_cap_by_max_daily_feed():
    feed = base_config().feed_plan[0]
    feed.maximum_daily_feed_kg = 10

    result = core.simulate(base_config(feed_plan=[feed]))

    assert result.daily_results[0].actual_feed_kg == 10


def test_feed_cap_by_max_adg():
    result = core.simulate(base_config(target_fcr=2, maximum_adg_g_per_day=0.1))

    assert result.daily_results[0].actual_feed_kg == 20


def test_early_stop_at_final_carrying_capacity():
    result = core.simulate(
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
    result = core.simulate(
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
    baseline = core.simulate(profitable_partial_config())
    optimized = core.optimize_partial_harvests(profitable_partial_config())

    assert len(optimized.partial_harvests) >= 1
    assert optimized.profit_per_day > baseline.profit_per_day


def test_prediction_output_summary_includes_initial_abw():
    config = base_config(initial_abw_g=8, final_doc=32)
    result = core.simulate(config)
    out = prediction.result_to_out(date(2026, 5, 1), result, config.feed_plan)

    assert out.summary.initial_abw_g == Decimal("8.0000")


@pytest.mark.asyncio
async def test_build_config_uses_target_day_abw_sample(monkeypatch):
    async def fake_baseline(db, cycle, start_date):
        return {
            "previous_biomass_kg": Decimal("1294"),
            "feed_since_previous_sample_start_kg": Decimal("0"),
            "estimated_population": 100_000,
            "harvested_biomass_since_previous_sample_kg": Decimal("0"),
            "initial_abw_g": Decimal("17.0605"),
        }

    async def fake_farm_and_area(db, cycle):
        return uuid4(), Decimal("1000")

    async def fake_feed_plan(db, farm_id, config_data, cycle):
        return base_config().feed_plan

    async def fake_cumulative_feed_before(db, cycle_id, start_date):
        return Decimal("1234")

    monkeypatch.setattr(prediction, "get_prediction_baseline", fake_baseline)
    monkeypatch.setattr(prediction, "_farm_and_area", fake_farm_and_area)
    monkeypatch.setattr(prediction, "_feed_plan", fake_feed_plan)
    monkeypatch.setattr(prediction, "_cumulative_feed_before", fake_cumulative_feed_before)
    cycle = SimpleNamespace(
        id=uuid4(),
        pond_id=uuid4(),
        start_date=date(2026, 5, 1),
        prediction_config={},
        feeding_index_increment=Decimal("0.01"),
        maximum_feeding_index=Decimal("0.7"),
    )

    config = await prediction.build_config(
        SimpleNamespace(),
        cycle,
        date(2026, 7, 8),
        70,
    )

    assert config.initial_abw_g == 17.0605
    assert config.initial_cumulative_feed_kg == 1234


class _ScalarResult:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _ExecuteResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _ScalarResult(self.values)


class _ApplyPredictionDb:
    def __init__(self, existing_logs):
        self.existing_logs = existing_logs
        self.statements = []
        self.added = []
        self.flushed = False
        self.committed = False

    async def execute(self, stmt):
        self.statements.append(stmt)
        if len(self.statements) == 1:
            return _ExecuteResult(self.existing_logs)
        return _ExecuteResult([])

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True


def _prediction_daily_row(day, doc, starting_abw, ending_abw):
    return PredictionDailyRowOut(
        date=day,
        doc=doc,
        feed_name="",
        feeding_index=Decimal("0"),
        starting_population=1000,
        ending_population=1000,
        starting_abw_g=Decimal(starting_abw),
        ending_abw_g=Decimal(ending_abw),
        starting_biomass_kg=Decimal("8"),
        ending_biomass_kg=Decimal("8"),
        actual_feed_kg=Decimal("0"),
        cumulative_feed_kg=Decimal("0"),
        count_size=Decimal("125"),
        harvest_price_per_kg=Decimal("1"),
        partial_harvest_kg=Decimal("0"),
        stop_reason="",
        feedings=[],
    )


@pytest.mark.asyncio
async def test_apply_prediction_preserves_start_day_abw_sampling():
    start = date(2026, 5, 10)
    final = date(2026, 5, 11)
    start_log = DailyLog(
        id=uuid4(),
        cycle_id=uuid4(),
        date=start,
        abw_g=Decimal("8.2000"),
        abw_sample_time=time(5, 0),
    )
    future_log = DailyLog(id=uuid4(), cycle_id=start_log.cycle_id, date=final)
    db = _ApplyPredictionDb([start_log, future_log])
    preview = PredictionResultOut(
        summary=PredictionSummaryOut(
            initial_abw_g=Decimal("8.2000"),
            final_doc=11,
            final_date=final,
            final_abw_g=Decimal("9.0000"),
            final_biomass_kg=Decimal("9"),
            total_harvested_biomass_kg=Decimal("9"),
            cumulative_feed_kg=Decimal("0"),
            simulated_feed_kg=Decimal("0"),
            final_revenue=Decimal("0"),
            partial_revenue=Decimal("0"),
            total_revenue=Decimal("0"),
            feed_cost=Decimal("0"),
            total_costs=Decimal("0"),
            profit=Decimal("0"),
            profit_per_day=Decimal("0"),
            harvest_count_size=Decimal("111.11"),
            harvest_price_per_kg=Decimal("0"),
            stop_reason="final_doc",
        ),
        daily_rows=[
            _prediction_daily_row(start, 10, "8.2000", "8.5000"),
            _prediction_daily_row(final, 11, "8.5000", "9.0000"),
        ],
        partial_harvests=[],
        generated=None,
    )

    result = await prediction.apply_prediction_result(
        db,
        SimpleNamespace(id=start_log.cycle_id),
        preview,
    )

    assert start_log.abw_g == Decimal("8.2000")
    assert start_log.abw_sample_time == time(5, 0)
    assert all(item is not start_log for item in db.added)
    assert result.generated.daily_logs_deleted == 1
    assert db.flushed
    assert db.committed


@pytest.mark.asyncio
async def test_apply_prediction_anchors_partial_harvest_abw_and_final_harvest():
    from app.models import Harvest

    start = date(2026, 5, 10)
    partial_day = date(2026, 5, 11)
    final = date(2026, 5, 12)
    db = _ApplyPredictionDb([])
    preview = PredictionResultOut(
        summary=PredictionSummaryOut(
            initial_abw_g=Decimal("8.0000"),
            final_doc=12,
            final_date=final,
            final_abw_g=Decimal("10.0000"),
            final_biomass_kg=Decimal("500"),
            total_harvested_biomass_kg=Decimal("900"),
            cumulative_feed_kg=Decimal("0"),
            simulated_feed_kg=Decimal("0"),
            final_revenue=Decimal("1000"),
            partial_revenue=Decimal("400"),
            total_revenue=Decimal("1400"),
            feed_cost=Decimal("0"),
            total_costs=Decimal("0"),
            profit=Decimal("0"),
            profit_per_day=Decimal("0"),
            harvest_count_size=Decimal("100"),
            harvest_price_per_kg=Decimal("2"),
            stop_reason="final_doc",
        ),
        daily_rows=[
            _prediction_daily_row(start, 10, "8.0000", "8.5000"),
            _prediction_daily_row(partial_day, 11, "9.0000", "9.5000"),
            _prediction_daily_row(final, 12, "9.5000", "10.0000"),
        ],
        partial_harvests=[
            PredictionPartialHarvestOut(
                date=partial_day,
                doc=11,
                biomass_kg=Decimal("400"),
                sampled_abw_g=Decimal("9.0000"),
                count_size=Decimal("111.11"),
                price_per_kg=Decimal("1"),
                total_price=Decimal("400"),
                estimated_count=44444,
            )
        ],
        generated=None,
    )

    result = await prediction.apply_prediction_result(
        db,
        SimpleNamespace(id=uuid4()),
        preview,
    )

    logs = {item.date: item for item in db.added if isinstance(item, DailyLog)}
    # Partial harvest day carries an ABW anchor equal to the harvest ABW.
    assert logs[partial_day].abw_g == Decimal("9.0000")
    assert logs[partial_day].abw_sample_time == time(5, 0)
    # Final day carries the ending ABW anchor.
    assert logs[final].abw_g == Decimal("10.0000")

    harvests = [item for item in db.added if isinstance(item, Harvest)]
    # One partial harvest plus the final harvest of the standing crop.
    assert len(harvests) == 2
    final_harvest = next(h for h in harvests if h.notes == "Predicted final harvest")
    assert final_harvest.biomass_kg == Decimal("500")
    assert final_harvest.sampled_abw_g == Decimal("10.0000")
    assert result.generated.feedings_created == 0
