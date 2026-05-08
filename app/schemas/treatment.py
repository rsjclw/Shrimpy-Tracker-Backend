import uuid
from datetime import time as dtime

from pydantic import BaseModel, ConfigDict


class TreatmentCreate(BaseModel):
    treatment_time: dtime
    action: str
    worker: str | None = None
    notes: str | None = None


class TreatmentUpdate(BaseModel):
    treatment_time: dtime | None = None
    action: str | None = None
    worker: str | None = None
    notes: str | None = None


class TreatmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    daily_log_id: uuid.UUID
    treatment_time: dtime
    action: str
    worker: str | None
    notes: str | None
