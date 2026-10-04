from datetime import date, time, timedelta
from decimal import Decimal

from app.services import metrics as M
from app.services.finish_check import finish_check

H = date(2026, 9, 29)  # final harvest day
FEEDS = [M.FeedingRow(date=date(2026, 8, 25), amount_kg=Decimal("500"), feed_time=time(7, 0)), M.FeedingRow(date=date(2026, 9, 9), amount_kg=Decimal("500"), feed_time=time(7, 0))]
HARVESTS = [
    M.HarvestRow(date=H, harvest_time=time(9, 0), biomass_kg=Decimal("600"), estimated_count=30_000),
    M.HarvestRow(date=H, harvest_time=time(14, 30), biomass_kg=Decimal("800"), estimated_count=40_000),
]
FINAL_SAMPLE = M.AbwRow(date=H, abw_g=Decimal("20"), sample_time=time(14, 31))


def _check(end=H, feeds=FEEDS, harvests=HARVESTS, abw=(FINAL_SAMPLE,)):
    return finish_check(end, 100_000, list(feeds), list(harvests), list(abw))


def test_the_right_order_gives_survival_and_cycle_fcr_and_nothing_to_fix():
    c = _check()
    assert (c.harvested_count, c.survival_rate_pct, c.cycle_fcr) == (70_000, Decimal("70.0"), Decimal("0.71"))
    assert (c.last_harvest_date, c.last_harvest_time) == (H, time(14, 30))
    assert c.harvests_after_end == 0
    assert c.feeds_after_final_harvest == []
    assert c.sample_before_final_harvest is None


def test_a_feed_left_after_the_final_harvest_is_flagged():
    c = _check(feeds=[*FEEDS, M.FeedingRow(date=H, amount_kg=Decimal("50"), feed_time=time(18, 0))])
    assert [(f.feed_time, f.amount_kg) for f in c.feeds_after_final_harvest] == [(time(18, 0), Decimal("50"))]
    assert c.cycle_fcr == Decimal("0.75")


def test_a_feed_before_the_final_harvest_is_fine():
    c = _check(feeds=[*FEEDS, M.FeedingRow(date=H, amount_kg=Decimal("20"), feed_time=time(6, 0))])
    assert c.feeds_after_final_harvest == []


def test_a_sample_taken_before_the_final_harvest_is_flagged():
    c = _check(abw=(M.AbwRow(date=H, abw_g=Decimal("20"), sample_time=time(9, 1)),))
    assert c.sample_before_final_harvest == time(9, 1)


def test_ending_after_the_final_harvest_day_points_back_to_it():
    c = _check(end=H + timedelta(days=3))
    assert c.last_harvest_date == H  # the dialog offers this day
    assert c.survival_rate_pct == Decimal("70.0")
    assert c.feeds_after_final_harvest == [] and c.sample_before_final_harvest is None


def test_ending_before_the_final_harvest_is_caught():
    c = _check(end=H - timedelta(days=1))
    assert c.harvests_after_end == 2
    assert c.survival_rate_pct == Decimal("0.0")
    assert c.cycle_fcr is None


def test_no_harvest_at_all():
    c = _check(harvests=[], abw=())
    assert (c.harvested_count, c.survival_rate_pct, c.cycle_fcr, c.last_harvest_date) == (0, Decimal("0.0"), None, None)
