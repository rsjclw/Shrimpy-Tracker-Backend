import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PondCreate(BaseModel):
    grid_id: uuid.UUID
    name: str
    area_m2: Decimal | None = None


class PondUpdate(BaseModel):
    name: str
    area_m2: Decimal | None = None


class PondOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    grid_id: uuid.UUID
    name: str
    area_m2: Decimal | None
