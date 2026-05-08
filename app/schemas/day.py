import uuid
from datetime import date as ddate
from datetime import time as dtime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.feeding import FeedingOut
from app.schemas.feeding import FeedingFeedType
from app.schemas.harvest import HarvestOut
from app.schemas.treatment import TreatmentOut
from app.schemas.water import WaterParametersOut


class DailyLogUpdate(BaseModel):
    abw_g: Decimal | None = None
    abw_sample_time: dtime | None = None
    notes: str | None = None


class DayMetrics(BaseModel):
    doc: int
    daily_feed_kg: Decimal
    cumulative_feed_kg: Decimal
    cumulative_feed_start_kg: Decimal
    cumulative_feed_end_kg: Decimal
    abw_g: Decimal | None
    estimated_population: int | None
    estimated_biomass_kg: Decimal | None
    harvest_biomass_kg: Decimal
    fcr: Decimal | None


class SamplingMetrics(BaseModel):
    adg_g_per_day: Decimal | None
    abw_gain_g: Decimal | None
    feed_since_previous_sample_kg: Decimal | None
    sample_fcr: Decimal | None


class DaySummary(BaseModel):
    """Lean summary for date-range listings."""

    date: ddate
    doc: int
    daily_feed_kg: Decimal
    abw_g: Decimal | None
    estimated_population: int | None
    estimated_biomass_kg: Decimal | None
    harvest_biomass_kg: Decimal
    fcr: Decimal | None


class DayView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    daily_log_id: uuid.UUID | None
    cycle_id: uuid.UUID
    date: ddate
    abw_g: Decimal | None
    abw_sample_time: dtime | None
    notes: str | None
    sampling: SamplingMetrics
    default_feed_types: list[FeedingFeedType]
    feedings: list[FeedingOut]
    harvests: list[HarvestOut]
    water: WaterParametersOut | None
    treatments: list[TreatmentOut]
    metrics: DayMetrics


class TrendPoint(BaseModel):
    date: ddate
    value: Decimal | None
    is_future: bool
    is_sampling_day: bool


class TrendSeries(BaseModel):
    metric: str
    points: list[TrendPoint]
