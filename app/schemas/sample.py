import uuid
from datetime import date as ddate

from pydantic import BaseModel, ConfigDict


class PopulationSampleCreate(BaseModel):
    date: ddate
    population: int
    method: str | None = None
    notes: str | None = None


class PopulationSampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cycle_id: uuid.UUID
    date: ddate
    population: int
    method: str | None
    notes: str | None
