from datetime import date, time, timedelta
from decimal import Decimal

from app.services.blind_feeding import (
    TARGET_SAMPLE_TIME,
    blind_feeding_window,
    daily_amount_kg,
    plan_blind_feeding,
)

START = date(2026, 9, 1)
RATES = [1.0, 1.5, 2.0]


def _plan(start=START, population=200_000, rates=RATES, target=Decimal("0.8")):
    return plan_blind_feeding(start, population, rates, time(6, 0), target)


def test_plan_feeds_one_day_per_template_rate_from_the_start_date():
    plan = _plan()
    assert [d.date for d in plan.days] == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    assert [d.doc for d in plan.days] == [1, 2, 3]


def test_each_day_splits_into_four_sessions_from_the_feed_time():
    day = _plan().days[0]
    assert [t for t, _ in day.sessions] == [time(6, 0), time(10, 0), time(14, 0), time(18, 0)]
    # 1.0 kg per 100k x 200k = 2.0 kg, split 25/30/30/15
    assert [kg for _, kg in day.sessions] == [Decimal("0.5"), Decimal("0.6"), Decimal("0.6"), Decimal("0.3")]


def test_amount_scales_with_population():
    assert daily_amount_kg(1.5, 200_000) == Decimal("3.0")
    assert daily_amount_kg(1.5, 100_000) == Decimal("1.5")
    small = _plan(population=100_000).days[2]
    big = _plan(population=200_000).days[2]
    assert sum(kg for _, kg in big.sessions) == 2 * sum(kg for _, kg in small.sessions)


def test_target_sample_is_the_day_after_the_template_at_dawn():
    plan = _plan()
    assert plan.target_sample == (date(2026, 9, 4), Decimal("0.8"))
    assert TARGET_SAMPLE_TIME == time(5, 0)


def test_no_target_means_no_sample():
    assert _plan(target=None).target_sample is None


def test_moving_the_start_moves_every_day_and_the_sample_but_not_the_doc():
    moved = _plan(start=START + timedelta(days=3))
    assert [d.date for d in moved.days] == [date(2026, 9, 4), date(2026, 9, 5), date(2026, 9, 6)]
    assert [d.doc for d in moved.days] == [1, 2, 3]
    assert moved.target_sample == (date(2026, 9, 7), Decimal("0.8"))
    assert [d.sessions for d in moved.days] == [d.sessions for d in _plan().days]


def test_window_covers_exactly_the_template_days():
    assert blind_feeding_window(START, 3) == (date(2026, 9, 1), date(2026, 9, 3))
    assert blind_feeding_window(START, 1) == (START, START)


def test_no_template_has_no_window():
    assert blind_feeding_window(START, 0) is None
