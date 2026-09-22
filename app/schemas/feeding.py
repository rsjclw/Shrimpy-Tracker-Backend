import uuid
from datetime import datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.feeding_amounts import round_feed_amount_kg


class FeedingAdditive(BaseModel):
    """An additive on a feeding, as sent by a client.

    Identify it by `additive_id` (the farm catalog id) or by `name` (matched to
    the farm's catalog, ignoring case). `dosage_gr_per_kg` is optional: when it
    is left out the server uses the last dose given for that additive in this
    cycle, then the catalog's default dose.
    """

    additive_id: int | None = None
    name: str | None = None
    dosage_gr_per_kg: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def needs_identity(self) -> "FeedingAdditive":
        if self.additive_id is None and not (self.name or "").strip():
            raise ValueError("Each additive needs an additive_id or a name")
        return self


class FeedingAdditiveOut(BaseModel):
    """An additive as stored on a feeding. `additive_id` is null only for legacy
    entries whose name matched nothing in the farm catalog."""

    additive_id: int | None = None
    name: str
    dosage_gr_per_kg: Decimal
    # dose x the feeding's amount, filled in by FeedingOut.
    amount_g: Decimal | None = None


class FeedingFeedType(BaseModel):
    feed_type_id: str
    brand: str
    type: str
    price_per_kg: Decimal
    percentage: Decimal
    notes: str | None = None


def _validate_feed_type_percentages(feed_types: list[FeedingFeedType] | None) -> None:
    if not feed_types:
        return
    total = sum((f.percentage for f in feed_types), Decimal("0"))
    if total != Decimal("100"):
        raise ValueError("Feed type percentages must total 100")


class FeedingCreate(BaseModel):
    feed_time: dtime
    amount_kg: Decimal
    duration_min: int | None = None
    additives: list[FeedingAdditive] = Field(default_factory=list)
    feed_types: list[FeedingFeedType] = Field(default_factory=list)
    notes: str | None = None
    updated_by: str | None = None
    updated_by_type: Literal["human", "ai"] | None = None

    @field_validator("amount_kg")
    @classmethod
    def round_amount_kg(cls, value: Decimal) -> Decimal:
        return round_feed_amount_kg(value)

    @model_validator(mode="after")
    def validate_feed_types(self) -> "FeedingCreate":
        _validate_feed_type_percentages(self.feed_types)
        return self


class FeedingUpdate(BaseModel):
    feed_time: dtime | None = None
    amount_kg: Decimal | None = None
    duration_min: int | None = None
    additives: list[FeedingAdditive] | None = None
    feed_types: list[FeedingFeedType] | None = None
    notes: str | None = None
    updated_by: str | None = None
    updated_by_type: Literal["human", "ai"] | None = None

    @field_validator("amount_kg")
    @classmethod
    def round_amount_kg(cls, value: Decimal | None) -> Decimal | None:
        return round_feed_amount_kg(value) if value is not None else None

    @model_validator(mode="after")
    def validate_feed_types(self) -> "FeedingUpdate":
        _validate_feed_type_percentages(self.feed_types)
        return self


class FeedingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    daily_log_id: uuid.UUID
    feed_time: dtime
    amount_kg: Decimal
    duration_min: int | None
    additives: list[FeedingAdditiveOut]
    feed_types: list[FeedingFeedType]
    notes: str | None
    updated_at: datetime
    updated_by: str | None
    updated_by_type: str | None

    @model_validator(mode="after")
    def fill_additive_grams(self) -> "FeedingOut":
        for additive in self.additives:
            additive.amount_g = (additive.dosage_gr_per_kg * self.amount_kg).quantize(Decimal("0.1"))
        return self
