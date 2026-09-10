import uuid
from datetime import time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

DEFAULT_POND_FEED_TIME = time(6, 0)


class PondCreate(BaseModel):
    grid_id: uuid.UUID
    name: str
    area_m2: Decimal | None = None
    default_feed_time: time = DEFAULT_POND_FEED_TIME


class PondUpdate(BaseModel):
    name: str
    area_m2: Decimal | None = None
    default_feed_time: time = DEFAULT_POND_FEED_TIME


class PondOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    grid_id: uuid.UUID
    name: str
    area_m2: Decimal | None
    default_feed_time: time
