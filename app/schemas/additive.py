from datetime import date
from decimal import Decimal
from typing import Literal
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


class AdditiveDoseOut(BaseModel):
    """The dose a cycle is on for one catalog additive, as of a date."""

    additive_id: int
    name: str
    # Dose a new feeding gets when it leaves the dose out; null when there is none.
    dosage_gr_per_kg: Decimal | None
    source: Literal["last_used", "default", "none"]
    last_used_date: date | None
    default_dosage_gr_per_kg: Decimal | None


class AdditiveUsageOut(BaseModel):
    """One additive on one day of a cycle."""

    date: date
    additive_id: int | None
    name: str
    # Feed that carried the additive that day, and the additive given with it.
    feed_kg: Decimal
    amount_g: Decimal
    # amount_g / feed_kg: the day's average dose.
    dosage_gr_per_kg: Decimal
