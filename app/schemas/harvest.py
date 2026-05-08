import uuid
from datetime import time as dtime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class HarvestCreate(BaseModel):
    harvest_time: dtime
    biomass_kg: Decimal = Field(gt=0)
    sampled_abw_g: Decimal = Field(gt=0)
    price_per_kg: Decimal = Field(ge=0)
    notes: str | None = None


class HarvestUpdate(BaseModel):
    harvest_time: dtime | None = None
    biomass_kg: Decimal | None = Field(default=None, gt=0)
    sampled_abw_g: Decimal | None = Field(default=None, gt=0)
    price_per_kg: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


class HarvestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    daily_log_id: uuid.UUID
    harvest_time: dtime
    biomass_kg: Decimal
    sampled_abw_g: Decimal
    price_per_kg: Decimal
    estimated_count: int
    notes: str | None
