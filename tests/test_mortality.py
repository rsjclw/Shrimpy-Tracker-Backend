from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import DailyLogUpdate
from app.services.day_view import TREND_METRICS, mortality_series

COUNTS = {date(2026, 9, 2): 10, date(2026, 9, 4): 25, date(2026, 9, 5): 0}


def test_daily_series_has_gaps_where_nothing_was_logged():
    points = mortality_series(COUNTS, "mortality_count", date(2026, 9, 1), date(2026, 9, 5))
    assert [v for _, v in points] == [None, Decimal(10), None, Decimal(25), Decimal(0)]


def test_cumulative_starts_at_the_first_logged_day():
    points = mortality_series(COUNTS, "cumulative_mortality", date(2026, 9, 1), date(2026, 9, 5))
    assert [v for _, v in points] == [None, Decimal(10), Decimal(10), Decimal(35), Decimal(35)]


def test_cumulative_carries_what_came_before_the_window():
    points = mortality_series(COUNTS, "cumulative_mortality", date(2026, 9, 4), date(2026, 9, 6))
    assert [v for _, v in points] == [Decimal(35), Decimal(35), Decimal(35)]


def test_both_are_trend_metrics():
    assert {"mortality_count", "cumulative_mortality"} <= TREND_METRICS


def test_mortality_cannot_be_negative():
    with pytest.raises(ValidationError):
        DailyLogUpdate(mortality_count=-1)
    assert DailyLogUpdate(mortality_count=0).mortality_count == 0
