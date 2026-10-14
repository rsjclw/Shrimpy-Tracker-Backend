import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.units import ProductUnitIn

# Fixed on purpose: free-text categories drift ("Probiotik", "probiotic") and the grouping falls apart.
InventoryCategory = Literal[
    "feed",
    "supplements",
    "probiotics",
    "lime_minerals",
    "disinfectants",
    "medicine",
    "equipment",
    "other",
]

MovementKind = Literal["receive", "use", "count"]


def _required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class WarehousePonds(BaseModel):
    """The ponds that draw from a warehouse: the whole set, replacing what was there.

    A pond draws from one warehouse, so listing it here takes it off whichever
    warehouse had it before.
    """

    pond_ids: list[uuid.UUID] = Field(default_factory=list)


class WarehouseCreate(BaseModel):
    name: str = Field(max_length=100)
    notes: str | None = None

    _name = field_validator("name")(_required_text)


class WarehouseUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return _required_text(value) if value is not None else None


class NewStockProduct(BaseModel):
    """A product described as it is first stocked. Kind is always "product"."""

    name: str = Field(max_length=150)
    category: InventoryCategory
    base_unit: str = Field(max_length=30)
    price_per_unit: Decimal | None = Field(default=None, ge=0)
    units: list[ProductUnitIn] = Field(default_factory=list)

    _name = field_validator("name")(_required_text)


class InventoryItemCreate(BaseModel):
    """Stock something in this warehouse: one the farm already has, or a new one.

    Creating a product is only possible through here, so a product can never
    exist without a warehouse holding it.
    """

    product_id: uuid.UUID | None = None
    new_product: NewStockProduct | None = None
    quantity: Decimal = Field(default=Decimal("0"), ge=0)
    low_stock_level: Decimal | None = Field(default=None, ge=0)
    location_note: str | None = Field(default=None, max_length=100)
    notes: str | None = None

    @model_validator(mode="after")
    def one_or_the_other(self) -> "InventoryItemCreate":
        if (self.product_id is None) == (self.new_product is None):
            raise ValueError("Send either product_id or new_product, not both")
        return self


class InventoryItemUpdate(BaseModel):
    """Everything but the amount: stock only changes through movements, so each change is recorded.

    The product is not editable either - pointing a stock row at something else
    would silently rewrite what every past movement was counting.
    """

    low_stock_level: Decimal | None = Field(default=None, ge=0)
    location_note: str | None = Field(default=None, max_length=100)
    notes: str | None = None


class InventoryItemOut(BaseModel):
    """A stock row, with the product's own fields folded in so a client can render
    the inventory list without fetching the catalog as well."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    warehouse_id: uuid.UUID
    product_id: uuid.UUID
    quantity: Decimal
    low_stock_level: Decimal | None
    location_note: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    # Read-only, from the product. Never written back.
    name: str
    category: str
    unit: str

    @model_validator(mode="before")
    @classmethod
    def fold_in_product(cls, value: object) -> object:
        product = getattr(value, "product", None)
        if product is None:
            return value
        return {
            "id": value.id,
            "warehouse_id": value.warehouse_id,
            "product_id": value.product_id,
            "quantity": value.quantity,
            "low_stock_level": value.low_stock_level,
            "location_note": value.location_note,
            "notes": value.notes,
            "created_at": value.created_at,
            "updated_at": value.updated_at,
            "name": product.name,
            "category": product.category,
            "unit": product.base_unit,
        }


class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    grid_id: uuid.UUID
    name: str
    notes: str | None
    created_at: datetime
    # Ponds drawing from here; filled in by the router.
    pond_ids: list[uuid.UUID] = Field(default_factory=list)


class WarehouseInventoryOut(WarehouseOut):
    items: list[InventoryItemOut]



class _DeliveryDetails(BaseModel):
    """How a delivery came and what it cost. Pack sizes and prices change between
    deliveries, so they belong to the receive, not only to the product."""

    # One of the product's units, e.g. "sack". Its size defaults to that unit's size.
    pack_unit: str | None = Field(default=None, max_length=30)
    pack_count: Decimal | None = Field(default=None, gt=0)
    # Base units in one pack of this delivery; sent when it differs from the usual.
    pack_size: Decimal | None = Field(default=None, gt=0)
    # Rupiah for the whole delivery.
    total_cost: Decimal | None = Field(default=None, ge=0)
    supplier: str | None = Field(default=None, max_length=150)

    @field_validator("pack_unit", "supplier")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @model_validator(mode="after")
    def _packs_together(self):
        if (self.pack_unit is None) != (self.pack_count is None):
            raise ValueError("Send pack_unit and pack_count together")
        if self.pack_size is not None and self.pack_unit is None:
            raise ValueError("pack_size needs pack_unit and pack_count")
        return self

    def has_details(self) -> bool:
        return any(v is not None for v in (self.pack_unit, self.total_cost, self.supplier))


class MovementCreate(_DeliveryDetails):
    """receive/use take a positive amount; count sets the exact amount on hand (stock take).

    A receive may also say how it came (`pack_count` packs of `pack_unit`) and what it
    cost. `amount` may then be left out: it is the packs times their size.
    """

    kind: MovementKind
    amount: Decimal | None = Field(default=None, ge=0)
    note: str | None = None

    @model_validator(mode="after")
    def _details_are_for_receives(self) -> "MovementCreate":
        if self.kind != "receive" and self.has_details():
            raise ValueError("Packs, cost and supplier belong to a receive")
        if self.amount is None and self.pack_count is None:
            raise ValueError("Enter an amount")
        return self


class MovementUpdate(_DeliveryDetails):
    """A receive's delivery details, filled in afterwards: the bill is often paid a day
    later. The amount never changes here; correct stock with a use or a count."""

    note: str | None = None


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    kind: str
    delta: Decimal
    quantity_after: Decimal
    note: str | None
    pack_unit: str | None = None
    pack_size: Decimal | None = None
    pack_count: Decimal | None = None
    total_cost: Decimal | None = None
    supplier: str | None = None
    # What one base unit of this delivery cost.
    unit_cost: Decimal | None = None
    created_by: str | None
    created_at: datetime

    @model_validator(mode="after")
    def _unit_cost(self) -> "MovementOut":
        if self.kind == "receive" and self.total_cost is not None and self.delta > 0:
            self.unit_cost = (self.total_cost / self.delta).quantize(Decimal("0.01"))
        return self
