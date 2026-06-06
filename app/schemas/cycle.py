from datetime import date
from decimal import Decimal
import uuid

from pydantic import BaseModel, ConfigDict, Field


class PredictionCycleSettings(BaseModel):
    preparation_day: int = Field(ge=0)
    maximum_shrimp_size_g: Decimal = Field(gt=0)


class PredictionGrowthSettings(BaseModel):
    target_fcr: Decimal = Field(gt=0)
    maximum_adg_g_per_day: Decimal = Field(gt=0)
    initial_feeding_index: Decimal = Field(gt=0)
    feeding_index_increment: Decimal = Field(gt=0)
    maximum_feeding_index: Decimal = Field(gt=0)


class PredictionCapacitySettings(BaseModel):
    stable_carrying_capacity_kg_per_m2: Decimal = Field(gt=0)
    final_carrying_capacity_kg_per_m2: Decimal = Field(gt=0)


class PredictionHarvestSettings(BaseModel):
    minimum_partial_harvest_biomass_kg: Decimal = Field(gt=0)
    harvest_fixed_cost_per_event: Decimal = Field(ge=0)


class PredictionPricePoint(BaseModel):
    count_size: Decimal = Field(gt=0)
    price_per_kg: Decimal = Field(gt=0)


class PredictionPricesSettings(BaseModel):
    harvest_price_points: list[PredictionPricePoint] = Field(min_length=1)


class PredictionCostsSettings(BaseModel):
    pl_price_per_piece: Decimal = Field(ge=0)
    electricity_kwh: Decimal = Field(ge=0)
    electricity_price_per_kwh: Decimal = Field(ge=0)
    labor_cost_per_day: Decimal = Field(ge=0)
    probiotics_cost_per_day: Decimal = Field(ge=0)
    disinfection_cost_per_day: Decimal = Field(ge=0)
    liming_cost_per_day: Decimal = Field(ge=0)


class PredictionFeedPlanRow(BaseModel):
    feed_type_id: uuid.UUID
    maximum_daily_feed_kg: Decimal = Field(gt=0)
    use_until_abw_g: Decimal = Field(gt=0)


class PredictionConfig(BaseModel):
    cycle: PredictionCycleSettings
    growth: PredictionGrowthSettings
    capacity: PredictionCapacitySettings
    harvest: PredictionHarvestSettings
    prices: PredictionPricesSettings
    costs: PredictionCostsSettings
    feed_plan: list[PredictionFeedPlanRow] = Field(min_length=1)


class CycleCreate(BaseModel):
    pond_id: uuid.UUID
    name: str
    start_date: date
    planned_end_date: date | None = None
    initial_population: int
    initial_abw_g: Decimal
    maximum_daily_feed_capacity_kg: Decimal | None = None
    stable_carrying_capacity_kg_per_m3: Decimal | None = None
    final_carrying_capacity_kg_per_m3: Decimal | None = None
    feeding_index_increment: Decimal = Decimal("0.010")
    maximum_feeding_index: Decimal | None = None
    blind_feeding_template_id: uuid.UUID | None = None
    blind_feeding_target_abw_g: Decimal | None = None
    prediction_config: PredictionConfig | None = None
    notes: str | None = None


class CycleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pond_id: uuid.UUID
    name: str
    start_date: date
    planned_end_date: date | None
    actual_end_date: date | None
    initial_population: int
    initial_abw_g: Decimal
    maximum_daily_feed_capacity_kg: Decimal | None
    stable_carrying_capacity_kg_per_m3: Decimal | None
    final_carrying_capacity_kg_per_m3: Decimal | None
    feeding_index_increment: Decimal
    maximum_feeding_index: Decimal | None
    status: str
    notes: str | None
    blind_feeding_template_id: uuid.UUID | None
    blind_feeding_target_abw_g: Decimal | None
    prediction_config: PredictionConfig | None
