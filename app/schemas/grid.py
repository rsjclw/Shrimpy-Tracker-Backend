import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GridCreate(BaseModel):
    farm_id: uuid.UUID
    name: str
    notes: str | None = None


class GridUpdate(BaseModel):
    name: str
    notes: str | None = None


class GridOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    farm_id: uuid.UUID
    name: str
    notes: str | None
    created_at: datetime
