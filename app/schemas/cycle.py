from datetime import date
from decimal import Decimal
import uuid

from pydantic import BaseModel, ConfigDict


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
