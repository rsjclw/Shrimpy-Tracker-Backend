from datetime import date, time
from decimal import Decimal

from app.services import metrics as M
from app.services.cycle_summary import HarvestIn, cycle_summary

START = date(2026, 6, 1)
END = date(2026, 9, 8)  # DOC 100
PARTIAL = HarvestIn(date(2026, 8, 1), time(9, 0), Decimal("300"), Decimal("15"), 20_000, Decimal("12000000"))
FINAL = HarvestIn(END, time(10, 0), Decimal("1400"), Decimal("20"), 70_000, Decimal("70000000"))
ROWS = [M.HarvestRow(h.date, h.harvest_time, h.biomass_kg, h.count) for h in (PARTIAL, FINAL)]
FEEDS = [M.FeedingRow(date(2026, 7, 1), Decimal("400")), M.FeedingRow(date(2026, 8, 15), Decimal("700")), M.FeedingRow(date(2026, 8, 15), Decimal("100"))]
ABW = [M.AbwRow(date(2026, 8, 1), Decimal("15")), M.AbwRow(END, Decimal("20"), time(10, 1))]


def _summary(area=Decimal("1000"), ended=True, mortality=None, biomass=None):
    return cycle_summary(
        start_date=START,
        initial_population=100_000,
        area_m2=area,
        end_date=END,
        ended=ended,
        feedings=FEEDS,
        harvests=ROWS,
        harvest_details=[FINAL, PARTIAL],
        abw_history=ABW,
        mortality=mortality if mortality is not None else {date(2026, 7, 10): 40, date(2026, 7, 20): 120, date(2026, 9, 1): 15},
        daily_biomass=biomass if biomass is not None else {date(2026, 7, 31): Decimal("1650.4"), date(2026, 8, 30): Decimal("1820.06"), END: Decimal("0")},
    )


def test_basic_stats():
    s = _summary()
    assert s.total_harvest_kg == Decimal("1700")
    assert s.total_feed_kg == Decimal("1200")
    assert s.yield_t_per_1000m2 == Decimal("1.70")  # 1,700 kg on 1,000 m2
    assert s.fcr == Decimal("0.71")
    assert s.survival_rate_pct == Decimal("90.0")


def test_details():
    s = _summary()
    assert (s.initial_population, s.final_population, s.harvested_count) == (100_000, 70_000, 90_000)
    assert s.doc == 100 and s.final_abw_g == Decimal("20")
    assert s.average_adg_g_per_day == Decimal("0.20")  # 20 g over 100 days
    assert (s.max_biomass_kg, s.max_biomass_doc) == (Decimal("1820.1"), 91)
    assert s.max_carrying_capacity_kg_m2 == Decimal("1.82")
    assert (s.highest_daily_feed_kg, s.highest_daily_feed_doc) == (Decimal("800"), 76)
    assert (s.max_mortality, s.max_mortality_doc) == (120, 50)


def test_harvest_list_is_in_order_with_size_and_the_final_one_marked():
    lines = _summary().harvests
    assert [(h.date, h.size_pcs_per_kg, h.final) for h in lines] == [(PARTIAL.date, 67, False), (END, 50, True)]
    assert lines[1].doc == 100


def test_no_area_means_no_yield_or_carrying_capacity():
    s = _summary(area=None)
    assert s.yield_t_per_1000m2 is None and s.max_carrying_capacity_kg_m2 is None
    assert s.total_harvest_kg == Decimal("1700")


def test_without_mortality_records_max_mortality_is_empty():
    s = _summary(mortality={})
    assert s.max_mortality is None and s.max_mortality_doc is None


def test_a_running_cycle_has_no_survival_or_final_population_yet():
    s = _summary(ended=False)
    assert s.survival_rate_pct is None and s.final_population is None
    assert not any(h.final for h in s.harvests)
