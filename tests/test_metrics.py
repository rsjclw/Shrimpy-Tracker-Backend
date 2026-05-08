from datetime import date, datetime, time
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.metrics import (
    AbwRow,
    FeedingRow,
    HarvestRow,
    SampleRow,
    carry_forward_abw,
    cumulative_feed_before_date,
    cumulative_feed_kg,
    daily_feed_kg,
    doc_for,
    estimated_biomass_kg,
    estimated_population,
    estimated_population_at,
    estimated_population_end_of_day,
    estimated_abw_g,
    feed_between_samples,
    fcr,
    gain_fcr,
    adg_g_per_day,
)
from app.schemas.feeding import FeedingCreate


def test_doc_for_first_day_is_one():
    assert doc_for(date(2026, 5, 1), date(2026, 5, 1)) == 1


def test_doc_for_30_days_in():
    assert doc_for(date(2026, 5, 1), date(2026, 5, 31)) == 31


def test_daily_feed_sums_only_target_date():
    feedings = [
        FeedingRow(date(2026, 5, 1), Decimal("10")),
        FeedingRow(date(2026, 5, 1), Decimal("12.5")),
        FeedingRow(date(2026, 5, 2), Decimal("13")),
    ]
    assert daily_feed_kg(feedings, date(2026, 5, 1)) == Decimal("22.5")
    assert daily_feed_kg(feedings, date(2026, 5, 3)) == Decimal("0")


def test_cumulative_feed_includes_up_to_and_including():
    feedings = [
        FeedingRow(date(2026, 5, 1), Decimal("10")),
        FeedingRow(date(2026, 5, 2), Decimal("12")),
        FeedingRow(date(2026, 5, 3), Decimal("15")),
    ]
    assert cumulative_feed_kg(feedings, date(2026, 5, 2)) == Decimal("22")


def test_cumulative_feed_before_date_excludes_target_day():
    feedings = [
        FeedingRow(date(2026, 5, 1), Decimal("10"), time(18, 0)),
        FeedingRow(date(2026, 5, 2), Decimal("12"), time(6, 0)),
        FeedingRow(date(2026, 5, 2), Decimal("15"), time(18, 0)),
    ]
    assert cumulative_feed_before_date(feedings, date(2026, 5, 2)) == Decimal("10")


def test_estimated_population_uses_initial_when_no_samples():
    assert estimated_population(100_000, [], date(2026, 5, 5)) == 100_000


def test_estimated_population_uses_most_recent_sample():
    samples = [
        SampleRow(date(2026, 5, 5), 95_000),
        SampleRow(date(2026, 5, 12), 92_000),
    ]
    assert estimated_population(100_000, samples, date(2026, 5, 7)) == 95_000
    assert estimated_population(100_000, samples, date(2026, 5, 15)) == 92_000


def test_estimated_population_reduces_after_harvest_time_only():
    harvests = [HarvestRow(date(2026, 5, 5), time(10, 0), Decimal("100"), 10_000)]

    assert estimated_population_at(
        100_000, [], harvests, datetime(2026, 5, 5, 9, 59)
    ) == 100_000
    assert estimated_population_at(
        100_000, [], harvests, datetime(2026, 5, 5, 10, 0)
    ) == 90_000


def test_estimated_population_accumulates_harvests_and_floors_at_zero():
    harvests = [
        HarvestRow(date(2026, 5, 5), time(10, 0), Decimal("100"), 70_000),
        HarvestRow(date(2026, 5, 5), time(14, 0), Decimal("100"), 70_000),
    ]

    assert estimated_population_end_of_day(100_000, [], harvests, date(2026, 5, 5)) == 0


def test_population_sample_resets_harvest_anchor():
    samples = [SampleRow(date(2026, 5, 6), 50_000)]
    harvests = [
        HarvestRow(date(2026, 5, 5), time(10, 0), Decimal("100"), 20_000),
        HarvestRow(date(2026, 5, 6), time(10, 0), Decimal("50"), 5_000),
    ]

    assert estimated_population_end_of_day(100_000, samples, harvests, date(2026, 5, 6)) == 45_000


def test_carry_forward_abw_uses_initial_when_empty():
    assert carry_forward_abw(Decimal("0.01"), [], date(2026, 5, 1)) == Decimal("0.01")


def test_carry_forward_abw_uses_most_recent():
    history = [
        AbwRow(date(2026, 5, 7), Decimal("0.5")),
        AbwRow(date(2026, 5, 14), Decimal("1.2")),
    ]
    assert carry_forward_abw(Decimal("0.01"), history, date(2026, 5, 10)) == Decimal("0.5")


def test_estimated_abw_uses_beginning_of_day_feed_weighting_between_samples():
    feedings = [
        FeedingRow(date(2026, 5, 2), Decimal("10"), time(8, 0)),
        FeedingRow(date(2026, 5, 3), Decimal("30"), time(8, 0)),
        FeedingRow(date(2026, 5, 4), Decimal("60"), time(8, 0)),
    ]
    history = [
        AbwRow(date(2026, 5, 1), Decimal("1.0"), time(5, 0)),
        AbwRow(date(2026, 5, 5), Decimal("2.0"), time(5, 0)),
    ]

    assert estimated_abw_g(Decimal("0.01"), feedings, history, date(2026, 5, 3)) == Decimal("1.1000")


def test_estimated_abw_uses_previous_day_feed_for_next_day_state():
    feedings = [
        FeedingRow(date(2026, 5, 2), Decimal("12"), time(8, 0)),
        FeedingRow(date(2026, 5, 3), Decimal("20"), time(8, 0)),
        FeedingRow(date(2026, 5, 4), Decimal("20"), time(8, 0)),
        FeedingRow(date(2026, 5, 5), Decimal("20"), time(8, 0)),
        FeedingRow(date(2026, 5, 6), Decimal("48"), time(8, 0)),
    ]
    history = [
        AbwRow(date(2026, 5, 2), Decimal("1.0"), time(5, 0)),
        AbwRow(date(2026, 5, 7), Decimal("2.0"), time(5, 0)),
    ]

    assert estimated_abw_g(Decimal("0.01"), feedings, history, date(2026, 5, 3)) == Decimal("1.1000")


def test_estimated_abw_uses_sampled_abw_on_sampling_day():
    feedings = [
        FeedingRow(date(2026, 5, 5), Decimal("100"), time(18, 0)),
        FeedingRow(date(2026, 5, 6), Decimal("100"), time(8, 0)),
    ]
    history = [
        AbwRow(date(2026, 5, 1), Decimal("1.0"), time(5, 0)),
        AbwRow(date(2026, 5, 5), Decimal("2.0"), time(5, 0)),
        AbwRow(date(2026, 5, 8), Decimal("4.0"), time(5, 0)),
    ]

    assert estimated_abw_g(Decimal("0.01"), feedings, history, date(2026, 5, 5)) == Decimal("2.0")


def test_biomass_kg():
    assert estimated_biomass_kg(100_000, Decimal("10")) == Decimal("1000.000")


def test_fcr_returns_none_when_no_growth():
    assert fcr(Decimal("100"), Decimal("0")) is None


def test_fcr_basic():
    result = fcr(Decimal("1000"), Decimal("1000"))
    assert result == Decimal("1.00")


def test_fcr_includes_harvested_biomass():
    result = fcr(Decimal("1000"), Decimal("700"), Decimal("300"))
    assert result == Decimal("1.00")


def test_gain_fcr_uses_biomass_gain():
    assert gain_fcr(Decimal("500"), Decimal("1000"), Decimal("1250")) == Decimal("2.00")
    assert gain_fcr(Decimal("500"), Decimal("1000"), Decimal("1000")) is None


def test_gain_fcr_includes_harvested_biomass_between_samples():
    assert gain_fcr(Decimal("500"), Decimal("1000"), Decimal("800"), Decimal("450")) == Decimal("2.00")


def test_feed_type_percentages_must_total_100_when_present():
    FeedingCreate(
        feed_time=time(8, 0),
        amount_kg=Decimal("10"),
        feed_types=[
            {
                "feed_type_id": "a",
                "brand": "A",
                "type": "starter",
                "price_per_kg": Decimal("20"),
                "percentage": Decimal("25"),
            },
            {
                "feed_type_id": "b",
                "brand": "B",
                "type": "grower",
                "price_per_kg": Decimal("22"),
                "percentage": Decimal("75"),
            },
        ],
    )

    with pytest.raises(ValidationError):
        FeedingCreate(
            feed_time=time(8, 0),
            amount_kg=Decimal("10"),
            feed_types=[
                {
                    "feed_type_id": "a",
                    "brand": "A",
                    "type": "starter",
                    "price_per_kg": Decimal("20"),
                    "percentage": Decimal("20"),
                }
            ],
        )


def test_adg_g_per_day_uses_sample_times():
    previous = AbwRow(date(2026, 5, 1), Decimal("1.0"), time(5, 0))
    current = AbwRow(date(2026, 5, 3), Decimal("2.0"), time(17, 0))
    assert adg_g_per_day(previous, current) == Decimal("0.400")


def test_feed_between_samples_uses_feed_time_boundaries():
    previous = AbwRow(date(2026, 5, 1), Decimal("1.0"), time(5, 0))
    current = AbwRow(date(2026, 5, 2), Decimal("2.0"), time(5, 0))
    feedings = [
        FeedingRow(date(2026, 5, 1), Decimal("10"), time(4, 0)),
        FeedingRow(date(2026, 5, 1), Decimal("20"), time(6, 0)),
        FeedingRow(date(2026, 5, 2), Decimal("30"), time(5, 0)),
        FeedingRow(date(2026, 5, 2), Decimal("40"), time(6, 0)),
    ]
    assert feed_between_samples(feedings, previous, current) == Decimal("50")


def test_feed_between_samples_can_use_doc_one_anchor():
    previous = AbwRow(date(2026, 5, 1), Decimal("0.01"), time(0, 0))
    current = AbwRow(date(2026, 5, 3), Decimal("0.90"), time(5, 0))
    feedings = [
        FeedingRow(date(2026, 5, 1), Decimal("10"), time(8, 0)),
        FeedingRow(date(2026, 5, 2), Decimal("20"), time(8, 0)),
        FeedingRow(date(2026, 5, 3), Decimal("30"), time(6, 0)),
    ]

    assert feed_between_samples(feedings, previous, current) == Decimal("30")
    assert adg_g_per_day(previous, current) == Decimal("0.403")
