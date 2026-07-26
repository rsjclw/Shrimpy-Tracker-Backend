import uuid
from datetime import date as ddate
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class DayEnvironmentOut(BaseModel):
    """Cached daily weather for a grid."""

    model_config = ConfigDict(from_attributes=True)

    date: ddate
    temp_min_c: Decimal | None
    temp_max_c: Decimal | None
    temp_mean_c: Decimal | None
    # Global horizontal irradiance in MJ/m2 - the sun actually reaching the
    # pond surface, and the reason cloud cover alone is not enough.
    shortwave_radiation_sum_mj: Decimal | None
    sunshine_duration_hours: Decimal | None
    cloud_cover_daylight_pct: Decimal | None
    precipitation_mm: Decimal | None
    precipitation_hours: Decimal | None
    precipitation_probability_max_pct: Decimal | None
    is_forecast: bool
    source: str
    fetched_at: datetime | None


class GridEnvironmentOut(BaseModel):
    grid_id: uuid.UUID
    timezone: str | None
    days: list[DayEnvironmentOut]


class EnvironmentRefreshOut(BaseModel):
    grid_id: uuid.UUID
    days_written: int
    timezone: str | None
    synced_at: datetime | None
