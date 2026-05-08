import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class FeedTypeCreate(BaseModel):
    farm_id: uuid.UUID
    brand: str
    type: str
    price_per_kg: Decimal
    notes: str | None = None


class FeedTypeUpdate(BaseModel):
    brand: str | None = None
    type: str | None = None
    price_per_kg: Decimal | None = None
    notes: str | None = None


class FeedTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    farm_id: uuid.UUID
    brand: str
    type: str
    price_per_kg: Decimal
    notes: str | None
    created_at: datetime
