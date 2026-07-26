"""Weather parsing and sync-schedule tests.

The payload mirrors a real Open-Meteo response (units and key names captured
from api.open-meteo.com), trimmed to a few days so expected values can be
worked out by hand.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.weather import parse_forecast
from app.services.weather_sync import is_due, most_recent_slot, sync_hours

TODAY = datetime.now(timezone.utc).date()
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


def _hourly_day(day: date, covers: list[int], radiation: list[float]) -> dict:
    return {
        "time": [f"{day.isoformat()}T{hour:02d}:00" for hour in range(len(covers))],
        "cloud_cover": covers,
        "shortwave_radiation": radiation,
    }


def _payload(**overrides) -> dict:
    # 4 hours per day: two dark, two lit. Cloud cover in the lit hours is
    # 40 and 60, so the daylight mean must be 50 - the dark 0s are excluded.
    hourly = {"time": [], "cloud_cover": [], "shortwave_radiation": []}
    for day in (YESTERDAY, TODAY, TOMORROW):
        chunk = _hourly_day(day, [0, 0, 40, 60], [0.0, 0.0, 300.0, 250.0])
        for key in hourly:
            hourly[key].extend(chunk[key])

    payload = {
        "latitude": -6.9,
        "longitude": 106.5,
        "utc_offset_seconds": 25200,
        "timezone": "Asia/Jakarta",
        "elevation": 356.0,
        "hourly": hourly,
        "daily": {
            "time": [YESTERDAY.isoformat(), TODAY.isoformat(), TOMORROW.isoformat()],
            "temperature_2m_max": [28.4, 28.7, 27.9],
            "temperature_2m_min": [19.4, 17.9, 18.2],
            "temperature_2m_mean": [22.9, 22.8, 22.8],
            "shortwave_radiation_sum": [22.84, 21.24, 21.43],
            "sunshine_duration": [41993.4, 40255.35, 40621.72],
            "precipitation_sum": [0.0, 3.5, 12.25],
            "precipitation_hours": [0.0, 2.0, 5.0],
            "precipitation_probability_max": [0, 20, 85],
        },
    }
    payload.update(overrides)
    return payload


# --- location metadata ------------------------------------------------------


def test_timezone_and_elevation_come_back_from_the_coordinates():
    """This is how Grid.timezone gets populated - no extra dependency."""
    result = parse_forecast(_payload())

    assert result.timezone == "Asia/Jakarta"
    assert result.elevation_m == Decimal("356.00")


# --- units ------------------------------------------------------------------


def test_sunshine_duration_converts_seconds_to_hours():
    result = parse_forecast(_payload())

    # 41993.4 s / 3600 = 11.665 h
    assert result.rows[0].sunshine_duration_hours == Decimal("11.66")


def test_radiation_is_kept_as_megajoules():
    result = parse_forecast(_payload())

    assert result.rows[0].shortwave_radiation_sum_mj == Decimal("22.84")


def test_temperatures_and_rain_are_carried_through():
    row = parse_forecast(_payload()).rows[1]

    assert row.temp_min_c == Decimal("17.90")
    assert row.temp_max_c == Decimal("28.70")
    assert row.temp_mean_c == Decimal("22.80")
    assert row.precipitation_mm == Decimal("3.50")
    assert row.precipitation_hours == Decimal("2.00")
    assert row.precipitation_probability_max_pct == Decimal("20.00")


# --- cloud cover aggregation ------------------------------------------------


def test_cloud_cover_averages_daylight_hours_only():
    """A 24-hour mean would be 25; the daylight-only mean is 50."""
    result = parse_forecast(_payload())

    assert result.rows[0].cloud_cover_daylight_pct == Decimal("50.00")


def test_cloud_cover_is_none_when_no_daylight_hours_reported():
    payload = _payload()
    payload["hourly"]["shortwave_radiation"] = [0.0] * len(
        payload["hourly"]["shortwave_radiation"]
    )

    assert parse_forecast(payload).rows[0].cloud_cover_daylight_pct is None


# --- forecast vs actual -----------------------------------------------------


def test_past_and_present_are_actuals_and_the_future_is_forecast():
    rows = {row.date: row for row in parse_forecast(_payload()).rows}

    assert rows[YESTERDAY].is_forecast is False
    assert rows[TODAY].is_forecast is False
    assert rows[TOMORROW].is_forecast is True


# --- malformed payloads -----------------------------------------------------


def test_missing_series_yields_none_rather_than_raising():
    payload = _payload()
    del payload["daily"]["precipitation_probability_max"]

    rows = parse_forecast(payload).rows

    assert len(rows) == 3
    assert all(row.precipitation_probability_max_pct is None for row in rows)


def test_short_series_is_padded_instead_of_truncating_days():
    """Open-Meteo omits trailing values rather than padding with nulls."""
    payload = _payload()
    payload["daily"]["temperature_2m_max"] = [28.4]

    rows = parse_forecast(payload).rows

    assert len(rows) == 3
    assert rows[0].temp_max_c == Decimal("28.40")
    assert rows[2].temp_max_c is None


def test_null_values_survive_parsing():
    payload = _payload()
    payload["daily"]["shortwave_radiation_sum"] = [None, 21.24, None]

    rows = parse_forecast(payload).rows

    assert rows[0].shortwave_radiation_sum_mj is None
    assert rows[1].shortwave_radiation_sum_mj == Decimal("21.24")


def test_empty_payload_gives_no_rows():
    assert parse_forecast({}).rows == []


# --- sync schedule ----------------------------------------------------------


def test_sync_hours_parses_the_setting():
    assert sync_hours() == [5, 17]


def test_most_recent_slot_is_this_mornings_five_am():
    now = datetime(2026, 7, 26, 3, 0, tzinfo=timezone.utc)  # 10:00 Jakarta
    slot = most_recent_slot(now, "Asia/Jakarta", [5, 17])

    assert slot == datetime(2026, 7, 25, 22, 0, tzinfo=timezone.utc)  # 05:00 WIB


def test_most_recent_slot_falls_back_to_yesterday_before_the_first_hour():
    now = datetime(2026, 7, 25, 21, 0, tzinfo=timezone.utc)  # 04:00 Jakarta
    slot = most_recent_slot(now, "Asia/Jakarta", [5, 17])

    assert slot == datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc)  # 17:00 prev day


def test_most_recent_slot_follows_the_grids_own_timezone():
    now = datetime(2026, 7, 26, 3, 0, tzinfo=timezone.utc)

    jakarta = most_recent_slot(now, "Asia/Jakarta", [5, 17])
    jayapura = most_recent_slot(now, "Asia/Jayapura", [5, 17])

    assert jakarta != jayapura


def _grid(**kwargs):
    base = dict(
        latitude=Decimal("-6.9"),
        longitude=Decimal("106.5"),
        timezone="Asia/Jakarta",
        weather_synced_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


NOW = datetime(2026, 7, 26, 3, 0, tzinfo=timezone.utc)  # 10:00 Jakarta


def test_a_grid_never_synced_is_due():
    assert is_due(_grid(), NOW, [5, 17])


def test_a_grid_synced_after_the_slot_is_not_due():
    synced = datetime(2026, 7, 25, 22, 30, tzinfo=timezone.utc)  # 05:30 WIB

    assert not is_due(_grid(weather_synced_at=synced), NOW, [5, 17])


def test_a_grid_synced_before_the_slot_is_due():
    synced = datetime(2026, 7, 25, 21, 0, tzinfo=timezone.utc)  # 04:00 WIB

    assert is_due(_grid(weather_synced_at=synced), NOW, [5, 17])


def test_a_container_down_overnight_fires_on_the_next_tick():
    """The reason the sweep compares slots instead of sleeping until 05:00."""
    stale = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)

    assert is_due(_grid(weather_synced_at=stale), NOW, [5, 17])


def test_naive_synced_at_is_treated_as_utc_not_crashed_on():
    naive = datetime(2026, 7, 25, 21, 0)

    assert is_due(_grid(weather_synced_at=naive), NOW, [5, 17])


@pytest.mark.parametrize("missing", ["latitude", "longitude"])
def test_a_grid_without_coordinates_is_never_due(missing):
    assert not is_due(_grid(**{missing: None}), NOW, [5, 17])
