import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

Latitude = Decimal | None
Longitude = Decimal | None

_LAT = Field(default=None, ge=Decimal("-90"), le=Decimal("90"))
_LON = Field(default=None, ge=Decimal("-180"), le=Decimal("180"))


class GridCreate(BaseModel):
    farm_id: uuid.UUID
    name: str
    notes: str | None = None
    latitude: Latitude = _LAT
    longitude: Longitude = _LON


class GridUpdate(BaseModel):
    name: str
    notes: str | None = None
    latitude: Latitude = _LAT
    longitude: Longitude = _LON


class GridOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    farm_id: uuid.UUID
    name: str
    notes: str | None
    created_at: datetime
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    # Both resolved from the coordinates by Open-Meteo, read-only to clients.
    timezone: str | None = None
    elevation_m: Decimal | None = None
    weather_synced_at: datetime | None = None
