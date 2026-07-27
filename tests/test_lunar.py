"""Lunar window tests.

These assert invariants derived from ephem itself rather than hardcoded
calendar dates, so they stay valid at any point in time. The one hand-checkable
anchor is `test_known_full_moon_matches_almanac`.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import ephem
import pytest

from app.services.lunar import (
    FULL_DAYS_AFTER,
    FULL_DAYS_BEFORE,
    LEAD_DAYS,
    NEW_DAYS_AFTER,
    NEW_DAYS_BEFORE,
    lunar_day,
)

JAKARTA = "Asia/Jakarta"


def _date_of_next(kind: str, after: date) -> date:
    """The local Jakarta date on which the next full/new moon falls."""
    finder = ephem.next_full_moon if kind == "full" else ephem.next_new_moon
    instant = finder(ephem.Date(datetime.combine(after, datetime.min.time())))
    utc = instant.datetime().replace(tzinfo=timezone.utc)
    return utc.astimezone(ZoneInfo(JAKARTA)).date()


ANCHOR = date(2026, 1, 15)


def _day_where(kind: str, predicate, span: int = 40):
    """First day within `span` whose signed distance satisfies `predicate`.

    Date offsets alone are not safe near the window edges: a syzygy can fall at
    any hour, so "peak + 2 days" lands anywhere in -2.5..-1.5. Selecting on the
    signed distance pins the assertion to the rule under test.

    Bands must be a full day wide - consecutive days differ by exactly 1.0, so
    anything narrower can fall between two samples and never match.
    """
    for offset in range(span):
        day = lunar_day(ANCHOR + timedelta(days=offset), JAKARTA)
        value = day.days_to_full if kind == "full" else day.days_to_new
        if predicate(value):
            return day
    raise AssertionError(f"no {kind} day matched within {span} days")


# --- illumination -----------------------------------------------------------


def test_illumination_peaks_at_full_and_bottoms_at_new():
    full = _date_of_next("full", ANCHOR)
    new = _date_of_next("new", ANCHOR)

    # Never exactly 1.0 or 0.0 except at an eclipse - so bound, don't equate.
    assert lunar_day(full, JAKARTA).illumination > 0.98
    assert lunar_day(new, JAKARTA).illumination < 0.02


def test_illumination_stays_in_unit_range_across_a_full_synodic_month():
    for offset in range(30):
        value = lunar_day(ANCHOR + timedelta(days=offset), JAKARTA).illumination
        assert 0.0 <= value <= 1.0


# --- the signed distance that drives everything -----------------------------


def test_days_to_full_changes_sign_across_the_peak():
    full = _date_of_next("full", ANCHOR)

    assert lunar_day(full - timedelta(days=2), JAKARTA).days_to_full > 0
    assert lunar_day(full + timedelta(days=2), JAKARTA).days_to_full < 0


def test_is_peak_marks_only_the_syzygy_date():
    full = _date_of_next("full", ANCHOR)

    assert lunar_day(full, JAKARTA).is_peak
    assert not lunar_day(full - timedelta(days=2), JAKARTA).is_peak
    assert not lunar_day(full + timedelta(days=2), JAKARTA).is_peak


def test_waxing_is_true_before_full_and_false_after():
    full = _date_of_next("full", ANCHOR)

    assert lunar_day(full - timedelta(days=3), JAKARTA).waxing
    assert not lunar_day(full + timedelta(days=3), JAKARTA).waxing


# --- windows ----------------------------------------------------------------


@pytest.mark.parametrize("kind,expected", [("full", "full"), ("new", "new")])
def test_window_covers_the_days_around_the_peak(kind, expected):
    peak = _date_of_next(kind, ANCHOR)

    for offset in (-2, -1, 0, 1):
        day = lunar_day(peak + timedelta(days=offset), JAKARTA)
        assert day.window == expected, f"{kind} {offset:+d} should be in window"


@pytest.mark.parametrize("kind", ["full", "new"])
def test_window_is_closed_outside_its_own_edges(kind):
    peak = _date_of_next(kind, ANCHOR)

    assert lunar_day(peak - timedelta(days=5), JAKARTA).window != kind
    assert lunar_day(peak + timedelta(days=4), JAKARTA).window != kind


@pytest.mark.parametrize("kind", ["full", "new"])
def test_window_reaches_four_days_before_the_peak(kind):
    day = _day_where(kind, lambda value: 3.0 < value <= 4.0)

    assert day.window == kind


@pytest.mark.parametrize("kind", ["full", "new"])
def test_window_has_closed_by_three_days_after_the_peak(kind):
    """Molting tails off after the peak, so the window ends earlier on that side."""
    day = _day_where(kind, lambda value: -3.0 <= value < -2.0)

    assert day.window != kind


def test_window_is_asymmetric_around_the_peak():
    assert FULL_DAYS_BEFORE > FULL_DAYS_AFTER
    assert NEW_DAYS_BEFORE > NEW_DAYS_AFTER


def test_window_edges_follow_the_constants_not_illumination():
    full = _date_of_next("full", ANCHOR)

    for offset in range(-6, 7):
        day = lunar_day(full + timedelta(days=offset), JAKARTA)
        in_full = -FULL_DAYS_AFTER <= day.days_to_full <= FULL_DAYS_BEFORE
        in_new = -NEW_DAYS_AFTER <= day.days_to_new <= NEW_DAYS_BEFORE
        expected = "full" if in_full else ("new" if in_new else None)
        assert day.window == expected


def test_a_high_illumination_day_outside_the_window_is_not_flagged():
    """The regression this design exists to prevent.

    Roughly 5 days before full the moon is still around 70% lit, and 2 days
    after it is over 90% - yet the first is outside the window and the second
    inside. No illumination cutoff can order those two correctly.
    """
    full = _date_of_next("full", ANCHOR)
    day = lunar_day(full - timedelta(days=5), JAKARTA)

    assert day.illumination > 0.60
    assert day.window is None


def test_illumination_ranks_the_window_edges_backwards():
    """Why illumination cannot express this window at all.

    The window opens 4 days before the peak and closes 2 days after it. At the
    opening edge the moon is around 82% lit; just past the closing edge it is
    around 94%. The day inside the window is dimmer than the day outside it, so
    any illumination cutoff would admit exactly the wrong one.
    """
    opening = _day_where("full", lambda value: 3.0 < value <= 4.0)
    just_closed = _day_where("full", lambda value: -3.0 <= value < -2.0)

    assert opening.window == "full"
    assert just_closed.window is None
    assert opening.illumination < just_closed.illumination


# --- alerts -----------------------------------------------------------------


@pytest.mark.parametrize("kind", ["full", "new"])
def test_alert_fires_on_the_approach_only(kind):
    peak = _date_of_next(kind, ANCHOR)

    assert lunar_day(peak - timedelta(days=2), JAKARTA).alert == kind
    # The tail of a window is not part of the run-up.
    assert lunar_day(peak + timedelta(days=2), JAKARTA).alert is None


def test_alert_is_quiet_well_before_the_lead_window():
    full = _date_of_next("full", ANCHOR)
    day = lunar_day(full - timedelta(days=6), JAKARTA)

    assert day.days_to_full > LEAD_DAYS
    assert day.alert is None


def test_dosing_is_prompted_as_soon_as_the_window_opens():
    """No dead band: entering the window and being told to dose coincide."""
    assert LEAD_DAYS >= FULL_DAYS_BEFORE
    assert LEAD_DAYS >= NEW_DAYS_BEFORE

    day = _day_where("full", lambda value: 3.0 < value <= 4.0)

    assert day.window == "full"
    assert day.alert == "full"


# --- timezone handling ------------------------------------------------------


def test_neighbouring_zones_agree_on_the_window():
    """Guards the naive-UTC conversion inside lunar_day."""
    full = _date_of_next("full", ANCHOR)

    for offset in (-2, -1, 0, 1):
        day = full + timedelta(days=offset)
        assert (
            lunar_day(day, "Asia/Jakarta").window
            == lunar_day(day, "Asia/Makassar").window
        )


def test_unknown_timezone_falls_back_instead_of_raising():
    day = lunar_day(ANCHOR, "Not/AZone")

    assert 0.0 <= day.illumination <= 1.0


def test_illumination_differs_from_a_naive_local_reading():
    """A tz-unaware implementation would give identical values everywhere."""
    far_apart = [
        lunar_day(ANCHOR, "Pacific/Kiritimati").illumination,
        lunar_day(ANCHOR, "Pacific/Midway").illumination,
    ]

    assert far_apart[0] != far_apart[1]


# --- almanac anchor ---------------------------------------------------------


def test_known_full_moon_matches_almanac():
    """2026-01-03 full moon, per timeanddate.com. Hand-checkable."""
    day = lunar_day(date(2026, 1, 3), JAKARTA)

    assert day.is_peak
    assert day.window == "full"
    assert day.illumination > 0.99
