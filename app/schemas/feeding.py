import uuid
from datetime import time as dtime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FeedingAdditive(BaseModel):
    name: str
    dosage_gr_per_kg: int


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
    additives: list[FeedingAdditive]
    feed_types: list[FeedingFeedType]
    notes: str | None
