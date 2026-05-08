from decimal import Decimal
import uuid

from pydantic import BaseModel, ConfigDict


class AdditiveCreate(BaseModel):
    farm_id: uuid.UUID
    name: str
    dosage_gr_per_kg: Decimal | None = None


class AdditiveUpdate(BaseModel):
    name: str | None = None
    dosage_gr_per_kg: Decimal | None = None


class AdditiveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    farm_id: uuid.UUID
    name: str
    dosage_gr_per_kg: Decimal | None
