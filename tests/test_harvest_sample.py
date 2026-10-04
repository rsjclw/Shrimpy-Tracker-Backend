from datetime import time
from decimal import Decimal

from app.services.harvest_sample import sample_from_harvests


def test_one_harvest_gives_its_abw_a_minute_later():
    assert sample_from_harvests([(time(9, 0), Decimal("600"), Decimal("20"))]) == (Decimal("20.00"), time(9, 1))


def test_several_harvests_weigh_by_count_and_come_after_the_last():
    # 1,400 kg over 30,000 + 32,000 shrimp, whatever order they were written in
    rows = [(time(14, 30), Decimal("800"), Decimal("25")), (time(9, 0), Decimal("600"), Decimal("20"))]
    assert sample_from_harvests(rows) == (Decimal("22.58"), time(14, 31))


def test_a_harvest_at_2359_keeps_the_sample_on_the_same_day():
    assert sample_from_harvests([(time(23, 59), Decimal("10"), Decimal("15"))]) == (Decimal("15.00"), time(23, 59))


def test_seconds_on_a_harvest_time_are_dropped():
    assert sample_from_harvests([(time(10, 0, 30), Decimal("100"), Decimal("20"))])[1] == time(10, 1)


def test_no_harvests_means_no_sample():
    assert sample_from_harvests([]) is None
    assert sample_from_harvests([(time(9, 0), Decimal("0"), Decimal("20"))]) is None
