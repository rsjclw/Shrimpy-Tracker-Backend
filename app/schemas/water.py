import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class WaterParametersUpsert(BaseModel):
    do_am: Decimal | None = None
    do_pm: Decimal | None = None
    ph_am: Decimal | None = None
    ph_pm: Decimal | None = None
    salinity: Decimal | None = None
    tan: Decimal | None = None
    nitrite: Decimal | None = None
    phosphate: Decimal | None = None
    calcium: Decimal | None = None
    magnesium: Decimal | None = None
    alkalinity: Decimal | None = None


class WaterParametersOut(WaterParametersUpsert):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    daily_log_id: uuid.UUID
