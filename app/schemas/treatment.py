import uuid
from datetime import time as dtime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TreatmentItemIn(BaseModel):
    """One product applied. `unit` defaults to the product's own base unit."""

    product_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    unit: str | None = Field(default=None, max_length=30)


class TreatmentItemOut(BaseModel):
    """A line as stored on the treatment, resolved when it was saved.

    `base_amount` is what actually left the warehouse for a stocked product; for a
    mixture it is the amount of mix applied, and the components it expanded into
    show up in the stock movements rather than here.
    """

    product_id: uuid.UUID
    name: str
    amount: Decimal
    unit: str
    base_amount: Decimal
    base_unit: str


class TreatmentCreate(BaseModel):
    """Either `action` (free text, as before) or `items` (products), or both.

    With `items` and a `warehouse_id`, the stock is deducted and `action` is
    filled in from the products when the caller did not write one.
    """

    treatment_time: dtime
    action: str | None = None
    worker: str | None = None
    notes: str | None = None
    warehouse_id: uuid.UUID | None = None
    items: list[TreatmentItemIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def needs_content(self) -> "TreatmentCreate":
        if not (self.action or "").strip() and not self.items:
            raise ValueError("A treatment needs an action or at least one product")
        if self.items and self.warehouse_id is None:
            raise ValueError("Say which warehouse the products came from")
        return self


class TreatmentUpdate(BaseModel):
    """Sending `items` replaces them: the old stock is put back, the new deducted."""

    treatment_time: dtime | None = None
    action: str | None = None
    worker: str | None = None
    notes: str | None = None
    warehouse_id: uuid.UUID | None = None
    items: list[TreatmentItemIn] | None = None


class TreatmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    daily_log_id: uuid.UUID
    treatment_time: dtime
    action: str
    worker: str | None
    notes: str | None
    warehouse_id: uuid.UUID | None = None
    items: list[TreatmentItemOut] = Field(default_factory=list)
