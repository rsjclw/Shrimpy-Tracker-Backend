from datetime import date as ddate
from datetime import time as dtime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.feeding import FeedingFeedType


class PredictionRequest(BaseModel):
    start_date: ddate
    target_doc: int = Field(ge=1)
    optimize_partial_harvests: bool = True


class PredictionFeedingOut(BaseModel):
    feed_time: dtime
    amount_kg: Decimal
    feed_types: list[FeedingFeedType]


class PredictionDailyRowOut(BaseModel):
    date: ddate
    doc: int
    feed_name: str
    feeding_index: Decimal
    starting_population: int
    ending_population: int
    starting_abw_g: Decimal
    ending_abw_g: Decimal
    starting_biomass_kg: Decimal
    ending_biomass_kg: Decimal
    actual_feed_kg: Decimal
    cumulative_feed_kg: Decimal
    count_size: Decimal
    harvest_price_per_kg: Decimal
    partial_harvest_kg: Decimal
    stop_reason: str
    feedings: list[PredictionFeedingOut]


class PredictionPartialHarvestOut(BaseModel):
    date: ddate
    doc: int
    biomass_kg: Decimal
    sampled_abw_g: Decimal
    count_size: Decimal
    price_per_kg: Decimal
    total_price: Decimal
    estimated_count: int


class PredictionSummaryOut(BaseModel):
    final_doc: int
    final_date: ddate
    final_abw_g: Decimal
    final_biomass_kg: Decimal
    total_harvested_biomass_kg: Decimal
    cumulative_feed_kg: Decimal
    simulated_feed_kg: Decimal
    final_revenue: Decimal
    partial_revenue: Decimal
    total_revenue: Decimal
    feed_cost: Decimal
    total_costs: Decimal
    profit: Decimal
    profit_per_day: Decimal
    harvest_count_size: Decimal
    harvest_price_per_kg: Decimal
    stop_reason: str


class PredictionGeneratedCounts(BaseModel):
    days: int
    feedings_created: int
    harvests_created: int
    daily_logs_deleted: int


class PredictionResultOut(BaseModel):
    summary: PredictionSummaryOut
    daily_rows: list[PredictionDailyRowOut]
    partial_harvests: list[PredictionPartialHarvestOut]
    generated: PredictionGeneratedCounts | None = None
