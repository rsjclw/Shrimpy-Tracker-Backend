from datetime import date, datetime, time
from decimal import Decimal
from types import SimpleNamespace

from app.services import metrics as M
from app.services.day_view import _compute_metrics, _compute_sampling_metrics

START = date(2026, 6, 1)
END = date(2026, 9, 1)
TODAY = date(2026, 10, 4)
FINAL = M.HarvestRow(date=END, harvest_time=time(10, 0), biomass_kg=Decimal("1200"), estimated_count=60_000)


def _cycle(status="completed", actual_end_date=END, planned_end_date=None):
    return SimpleNamespace(
        start_date=START,
        initial_population=100_000,
        initial_abw_g=Decimal("0.001"),
        status=status,
        actual_end_date=actual_end_date,
        planned_end_date=planned_end_date,
    )


FEEDINGS = [M.FeedingRow(date=date(2026, 7, 1), amount_kg=Decimal("400")), M.FeedingRow(date=date(2026, 8, 15), amount_kg=Decimal("600"))]
ABW = [M.AbwRow(date=date(2026, 8, 1), abw_g=Decimal("10")), M.AbwRow(date=END, abw_g=Decimal("20"), sample_time=time(10, 1))]


def test_end_date_of_running_closed_and_reopened_cycles():
    assert M.cycle_end_date(_cycle(status="active", actual_end_date=None), TODAY) is None
    assert M.cycle_end_date(_cycle(status="active"), TODAY) == END  # reopened keeps its end
    assert M.cycle_end_date(_cycle(), TODAY) == END
    assert M.cycle_end_date(_cycle(actual_end_date=None, planned_end_date=date(2026, 9, 10)), TODAY) == date(2026, 9, 10)


def test_pond_is_empty_at_the_last_harvest_of_the_end_day():
    early = M.HarvestRow(date=END, harvest_time=time(7, 0), biomass_kg=Decimal("100"), estimated_count=5_000)
    assert M.pond_empty_at(END, [early, FINAL]) == datetime(2026, 9, 1, 10, 0)


def test_without_a_harvest_that_day_the_pond_is_empty_at_its_end():
    earlier = M.HarvestRow(date=date(2026, 8, 1), harvest_time=time(9, 0), biomass_kg=Decimal("100"), estimated_count=5_000)
    assert M.pond_empty_at(END, [earlier]) == datetime.combine(END, time.max)


def test_running_cycle_is_never_empty():
    assert M.pond_empty_at(None, [FINAL]) is None


def test_population_is_zero_from_the_empty_moment_on():
    empty_at = datetime(2026, 9, 1, 10, 0)
    assert M.estimated_population_at(100_000, [], [FINAL], datetime(2026, 9, 1, 9, 59), empty_at) == 100_000
    assert M.estimated_population_at(100_000, [], [FINAL], datetime(2026, 9, 1, 10, 0), empty_at) == 0
    assert M.estimated_population_at(100_000, [], [FINAL], datetime(2026, 9, 1, 10, 0)) == 40_000  # without the rule


def test_survival_rate_is_harvested_over_stocked():
    assert M.survival_rate_pct(165_000, [FINAL, M.HarvestRow(date=END, harvest_time=time(8, 0), biomass_kg=Decimal("1"), estimated_count=75_000)], END) == Decimal("81.8")
    assert M.survival_rate_pct(100_000, [FINAL], date(2026, 8, 31)) == Decimal("0.0")
    assert M.survival_rate_pct(0, [FINAL], END) is None


def test_ended_cycle_last_day_is_empty_and_fcr_counts_only_harvested():
    m = _compute_metrics(_cycle(), END, FEEDINGS, [], ABW, [FINAL])
    assert m.estimated_population == 0
    assert m.estimated_biomass_kg == 0
    assert m.fcr == Decimal("0.83")  # 1000 kg feed / 1200 kg harvested, no phantom standing biomass
    assert m.harvested_count == 60_000
    assert m.survival_rate_pct == Decimal("60.0")


def test_days_before_the_end_are_unchanged():
    m = _compute_metrics(_cycle(), date(2026, 8, 31), FEEDINGS, [], ABW, [FINAL])
    assert m.estimated_population == 100_000
    assert m.survival_rate_pct is None


def test_running_cycle_metrics_are_unchanged():
    m = _compute_metrics(_cycle(status="active", actual_end_date=None), END, FEEDINGS, [], ABW, [FINAL])
    assert m.estimated_population == 40_000
    assert m.survival_rate_pct is None
    assert m.harvested_count == 60_000


def test_crashed_cycle_keeps_its_shrimp_until_the_end_day():
    partial = M.HarvestRow(date=date(2026, 8, 1), harvest_time=time(9, 0), biomass_kg=Decimal("200"), estimated_count=20_000)
    cycle = _cycle(status="crashed")
    assert _compute_metrics(cycle, date(2026, 8, 15), FEEDINGS, [], ABW, [partial]).estimated_population == 80_000
    assert _compute_metrics(cycle, END, FEEDINGS, [], ABW, [partial]).estimated_population == 0


def test_a_sample_after_the_final_harvest_sees_the_empty_pond():
    s = _compute_sampling_metrics(END, FEEDINGS, [], ABW, [FINAL], _cycle())
    previous_biomass = M.estimated_biomass_kg(100_000, Decimal("10"))
    assert s.sample_fcr == M.gain_fcr(Decimal("600"), previous_biomass, Decimal("0"), Decimal("1200"))
