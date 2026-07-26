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
    FULL_HALF_WIDTH_DAYS,
    LEAD_DAYS,
    NEW_HALF_WIDTH_DAYS,
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
def test_window_covers_the_two_day_shoulders(kind, expected):
    peak = _date_of_next(kind, ANCHOR)

    for offset in (-2, -1, 0, 1, 2):
        day = lunar_day(peak + timedelta(days=offset), JAKARTA)
        assert day.window == expected, f"{kind} {offset:+d} should be in window"


@pytest.mark.parametrize("kind", ["full", "new"])
def test_window_is_closed_four_days_out(kind):
    peak = _date_of_next(kind, ANCHOR)

    assert lunar_day(peak - timedelta(days=4), JAKARTA).window != kind
    assert lunar_day(peak + timedelta(days=4), JAKARTA).window != kind


def test_half_widths_are_respected_exactly():
    """The window edge follows the constants, not an illumination cutoff."""
    full = _date_of_next("full", ANCHOR)

    for offset in range(-6, 7):
        day = lunar_day(full + timedelta(days=offset), JAKARTA)
        in_full = abs(day.days_to_full) <= FULL_HALF_WIDTH_DAYS
        in_new = abs(day.days_to_new) <= NEW_HALF_WIDTH_DAYS
        expected = "full" if in_full else ("new" if in_new else None)
        assert day.window == expected


def test_a_high_illumination_day_outside_the_window_is_not_flagged():
    """The regression this design exists to prevent.

    Roughly 3.4 days before full the moon is ~90% lit. An illumination
    threshold of 0.85 would open the window three days early; distance to the
    syzygy does not.
    """
    full = _date_of_next("full", ANCHOR)
    day = lunar_day(full - timedelta(days=4), JAKARTA)

    assert day.illumination > 0.80
    assert day.window is None


# --- alerts -----------------------------------------------------------------


@pytest.mark.parametrize("kind", ["full", "new"])
def test_alert_fires_on_the_approach_only(kind):
    peak = _date_of_next(kind, ANCHOR)

    assert lunar_day(peak - timedelta(days=2), JAKARTA).alert == kind
    # The tail of a window is not a cue to start dosing.
    assert lunar_day(peak + timedelta(days=2), JAKARTA).alert is None


def test_alert_is_quiet_well_before_the_lead_window():
    full = _date_of_next("full", ANCHOR)
    day = lunar_day(full - timedelta(days=5), JAKARTA)

    assert day.days_to_full > LEAD_DAYS
    assert day.alert is None


# --- timezone handling ------------------------------------------------------


def test_neighbouring_zones_agree_on_the_window():
    """Guards the naive-UTC conversion inside lunar_day."""
    full = _date_of_next("full", ANCHOR)

    for offset in (-2, -1, 0, 1, 2):
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
