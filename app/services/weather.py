"""Open-Meteo weather for a grid's coordinates.

Split deliberately: `parse_forecast` is pure and carries all the unit and
aggregation logic, so it can be tested against a captured payload without a
database or a network. Everything below it is I/O.

Data is CC BY 4.0 and the free tier is non-commercial - the frontend footer
carries the required attribution.
"""
import logging
from dataclasses import dataclass
from datetime import date as ddate
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyEnvironment, Grid

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DAILY_FIELDS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "shortwave_radiation_sum",
    "sunshine_duration",
    "precipitation_sum",
    "precipitation_hours",
    "precipitation_probability_max",
)
# Open-Meteo publishes no daily cloud-cover aggregate, so we derive one from
# the hourly series - see _daylight_cloud_cover.
HOURLY_FIELDS = ("cloud_cover", "shortwave_radiation")

PAST_DAYS = 7
FORECAST_DAYS = 16
REQUEST_TIMEOUT = 20


@dataclass(frozen=True)
class EnvironmentRow:
    date: ddate
    temp_min_c: Decimal | None
    temp_max_c: Decimal | None
    temp_mean_c: Decimal | None
    shortwave_radiation_sum_mj: Decimal | None
    sunshine_duration_hours: Decimal | None
    cloud_cover_daylight_pct: Decimal | None
    precipitation_mm: Decimal | None
    precipitation_hours: Decimal | None
    precipitation_probability_max_pct: Decimal | None
    is_forecast: bool


@dataclass(frozen=True)
class WeatherResult:
    timezone: str | None
    elevation_m: Decimal | None
    rows: list[EnvironmentRow]


def _decimal(value: object, places: str = "0.01") -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal(places))
    except (InvalidOperation, ValueError):
        return None


def _daylight_cloud_cover(hourly: dict) -> dict[ddate, Decimal]:
    """Mean cloud cover over daylight hours only, per date.

    A 24-hour mean would fold in the night, when cloud cover tells you nothing
    about the sun reaching the pond. Daylight is taken as the hours with any
    shortwave radiation, which the same response already gives us.
    """
    times = hourly.get("time") or []
    covers = hourly.get("cloud_cover") or []
    radiation = hourly.get("shortwave_radiation") or []

    totals: dict[ddate, list[float]] = {}
    for stamp, cover, watts in zip(times, covers, radiation):
        if cover is None or not watts:
            continue
        try:
            day = ddate.fromisoformat(stamp[:10])
        except ValueError:
            continue
        totals.setdefault(day, []).append(float(cover))

    return {
        day: Decimal(str(sum(values) / len(values))).quantize(Decimal("0.01"))
        for day, values in totals.items()
        if values
    }


def _local_today(tz_name: str | None) -> ddate:
    try:
        zone = ZoneInfo(tz_name) if tz_name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    return datetime.now(zone).date()


def parse_forecast(payload: dict) -> WeatherResult:
    """Turn an Open-Meteo response into rows. Pure - no I/O, no clock beyond today."""
    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    tz_name = payload.get("timezone")
    today = _local_today(tz_name)
    cloud_by_day = _daylight_cloud_cover(payload.get("hourly") or {})

    def column(name: str) -> list:
        values = daily.get(name) or []
        # Open-Meteo omits some series (e.g. precipitation_probability for
        # archive dates) rather than padding them with nulls.
        return list(values) + [None] * (len(dates) - len(values))

    columns = {name: column(name) for name in DAILY_FIELDS}

    rows: list[EnvironmentRow] = []
    for index, stamp in enumerate(dates):
        try:
            day = ddate.fromisoformat(stamp)
        except ValueError:
            continue

        sunshine_seconds = columns["sunshine_duration"][index]
        rows.append(
            EnvironmentRow(
                date=day,
                temp_min_c=_decimal(columns["temperature_2m_min"][index]),
                temp_max_c=_decimal(columns["temperature_2m_max"][index]),
                temp_mean_c=_decimal(columns["temperature_2m_mean"][index]),
                shortwave_radiation_sum_mj=_decimal(
                    columns["shortwave_radiation_sum"][index]
                ),
                sunshine_duration_hours=(
                    _decimal(float(sunshine_seconds) / 3600.0)
                    if sunshine_seconds is not None
                    else None
                ),
                cloud_cover_daylight_pct=cloud_by_day.get(day),
                precipitation_mm=_decimal(columns["precipitation_sum"][index]),
                precipitation_hours=_decimal(columns["precipitation_hours"][index]),
                precipitation_probability_max_pct=_decimal(
                    columns["precipitation_probability_max"][index]
                ),
                is_forecast=day > today,
            )
        )

    return WeatherResult(
        timezone=tz_name,
        elevation_m=_decimal(payload.get("elevation")),
        rows=rows,
    )


async def _get(url: str, params: dict) -> dict | None:
    """Fetch a payload, or None. Never raises into a request path."""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Weather is context, never blocking - callers fall back to cache.
        logger.warning("Open-Meteo request failed (%s): %s", url, exc)
        return None


async def fetch_forecast(
    latitude: Decimal, longitude: Decimal, past_days: int = PAST_DAYS
) -> WeatherResult | None:
    payload = await _get(
        FORECAST_URL,
        {
            "latitude": float(latitude),
            "longitude": float(longitude),
            "daily": ",".join(DAILY_FIELDS),
            "hourly": ",".join(HOURLY_FIELDS),
            # Resolves the IANA zone from the coordinates and returns it on the
            # response - this is where Grid.timezone comes from.
            "timezone": "auto",
            "past_days": past_days,
            "forecast_days": FORECAST_DAYS,
        },
    )
    return parse_forecast(payload) if payload else None


async def fetch_archive(
    latitude: Decimal, longitude: Decimal, date_from: ddate, date_to: ddate
) -> WeatherResult | None:
    payload = await _get(
        ARCHIVE_URL,
        {
            "latitude": float(latitude),
            "longitude": float(longitude),
            "daily": ",".join(DAILY_FIELDS),
            "hourly": ",".join(HOURLY_FIELDS),
            "timezone": "auto",
            "start_date": date_from.isoformat(),
            "end_date": date_to.isoformat(),
        },
    )
    return parse_forecast(payload) if payload else None


async def _store(
    db: AsyncSession, grid: Grid, result: WeatherResult, source: str
) -> int:
    """Upsert rows and refresh the grid's resolved location metadata."""
    if result.timezone and result.timezone != grid.timezone:
        grid.timezone = result.timezone
    if result.elevation_m is not None and result.elevation_m != grid.elevation_m:
        grid.elevation_m = result.elevation_m

    now = datetime.now(timezone.utc)
    for row in result.rows:
        values = {
            "grid_id": grid.id,
            "source": source,
            "fetched_at": now,
            **{
                field: getattr(row, field)
                for field in (
                    "date",
                    "temp_min_c",
                    "temp_max_c",
                    "temp_mean_c",
                    "shortwave_radiation_sum_mj",
                    "sunshine_duration_hours",
                    "cloud_cover_daylight_pct",
                    "precipitation_mm",
                    "precipitation_hours",
                    "precipitation_probability_max_pct",
                    "is_forecast",
                )
            },
        }
        statement = pg_insert(DailyEnvironment).values(**values)
        # Yesterday's forecast row becomes today's actual on the next sync.
        await db.execute(
            statement.on_conflict_do_update(
                constraint="uq_daily_environment_grid_date",
                set_={
                    key: statement.excluded[key]
                    for key in values
                    if key not in {"grid_id", "date"}
                },
            )
        )

    grid.weather_synced_at = now
    await db.flush()
    return len(result.rows)


async def sync_grid(db: AsyncSession, grid: Grid, past_days: int = PAST_DAYS) -> int:
    """Refresh a grid's cached weather. Returns rows written; 0 on failure."""
    if grid.latitude is None or grid.longitude is None:
        return 0
    result = await fetch_forecast(grid.latitude, grid.longitude, past_days=past_days)
    if result is None:
        return 0
    return await _store(db, grid, result, "open-meteo")


async def backfill_grid(
    db: AsyncSession, grid: Grid, date_from: ddate, date_to: ddate
) -> int:
    """Fill history beyond the forecast API's 92-day reach, from ERA5."""
    if grid.latitude is None or grid.longitude is None:
        return 0
    result = await fetch_archive(grid.latitude, grid.longitude, date_from, date_to)
    if result is None:
        return 0
    return await _store(db, grid, result, "era5")


async def resolve_location(db: AsyncSession, grid: Grid) -> None:
    """Populate timezone/elevation right after coordinates are set.

    Without this the grid has no timezone until the first scheduled sync, and
    the lunar windows would sit on the default zone in the meantime.
    """
    await sync_grid(db, grid)
