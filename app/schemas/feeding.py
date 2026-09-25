import uuid
from datetime import datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.feeding_amounts import round_feed_amount_kg


class FeedingAdditive(BaseModel):
    """An additive on a feeding, as sent by a client.

    Identify it by `product_id` (a catalog entry) or by `name`, matched to the
    farm's catalog ignoring case.

    `dose_per_kg` is per kg of feed, in whatever that entry is dosed in - grams
    for a mass, millilitres for a volume. Left out, the server uses the last dose
    given for it in this cycle; there is no stored default.
    """

    product_id: uuid.UUID | None = None
    name: str | None = None
    dose_per_kg: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def needs_identity(self) -> "FeedingAdditive":
        if self.product_id is None and not (self.name or "").strip():
            raise ValueError("Each additive needs a product_id or a name")
        return self


class FeedingAdditiveOut(BaseModel):
    """An additive as stored on a feeding.

    `product_id` is null only for entries written before the catalogs merged,
    whose name matched nothing in the catalog.
    """

    model_config = ConfigDict(populate_by_name=True)

    product_id: uuid.UUID | None = None
    name: str
    # Older feedings stored this under other names; all three read the same value.
    dose_per_kg: Decimal = Field(
        validation_alias=AliasChoices("dose_per_kg", "dose_gr_per_kg", "dosage_gr_per_kg")
    )
    # What the dose was in when logged. Entries from before the unit followed the
    # product were all grams, so that is the fallback.
    dose_unit: str = "g"
    # dose x the feeding's amount, filled in by FeedingOut.
    amount_g: Decimal | None = None


class FeedingFeedType(BaseModel):
    """One feed on a feeding, and its share of the amount.

    `product_id`/`name`/`price_per_unit` are the current shape. Feedings written
    before the catalogs merged carry `feed_type_id`, `brand`, `type` and
    `price_per_kg` instead, so those are accepted and folded in rather than
    migrated - they are snapshots, not live references.
    """

    # A string, as `feed_type_id` was: this is JSONB, and the prediction engine
    # builds rows with synthetic ids that are not UUIDs.
    product_id: str | None = None
    name: str = ""
    price_per_unit: Decimal | None = None
    percentage: Decimal
    notes: str | None = None
    # Pre-merge shape, read-only.
    feed_type_id: str | None = None
    brand: str | None = None
    type: str | None = None
    price_per_kg: Decimal | None = None

    @model_validator(mode="after")
    def fold_in_legacy(self) -> "FeedingFeedType":
        if not self.name:
            self.name = " ".join(part for part in (self.brand, self.type) if part).strip()
        if self.price_per_unit is None:
            self.price_per_unit = self.price_per_kg
        return self


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
            additive.amount_g = (additive.dose_per_kg * self.amount_kg).quantize(Decimal("0.1"))
        return self
