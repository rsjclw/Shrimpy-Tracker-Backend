import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.inventory import InventoryCategory
from app.schemas.units import BaseUnit, ProductUnitIn, canonical_base_unit, dose_unit_for

# A product is bought and stocked. A treatment formula is what goes in the pond
# and is never stocked - applying it draws on the products it is made of.
ProductKind = Literal["product", "formula"]



def _required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class ProductUnitOut(ProductUnitIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class ProductComponentIn(BaseModel):
    """`quantity` is in the component's base unit, per 1 base unit of the parent."""

    component_product_id: uuid.UUID
    quantity: Decimal = Field(gt=0)


class ProductComponentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    component_product_id: uuid.UUID
    quantity: Decimal
    # Filled in by the router so a recipe reads without a second round trip.
    component_name: str | None = None
    component_base_unit: str | None = None


class ProductCreate(BaseModel):
    farm_id: uuid.UUID
    name: str = Field(max_length=150)
    category: InventoryCategory
    kind: ProductKind = "product"
    base_unit: BaseUnit
    price_per_unit: Decimal | None = Field(default=None, ge=0)
    tracked: bool = True
    active: bool = True
    notes: str | None = None
    units: list[ProductUnitIn] = Field(default_factory=list)
    components: list[ProductComponentIn] = Field(default_factory=list)

    _name = field_validator("name")(_required_text)
    _base = field_validator("base_unit", mode="before")(canonical_base_unit)


class ProductUpdate(BaseModel):
    """Sending `units` or `components` replaces that list outright; leaving it out keeps it."""

    name: str | None = Field(default=None, max_length=150)
    category: InventoryCategory | None = None
    kind: ProductKind | None = None
    base_unit: BaseUnit | None = None
    price_per_unit: Decimal | None = Field(default=None, ge=0)
    tracked: bool | None = None
    active: bool | None = None
    notes: str | None = None
    units: list[ProductUnitIn] | None = None
    components: list[ProductComponentIn] | None = None

    @field_validator("name")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return _required_text(value) if value is not None else None

    @field_validator("base_unit", mode="before")
    @classmethod
    def _base(cls, value: str | None) -> str | None:
        return canonical_base_unit(value) if value is not None else None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    farm_id: uuid.UUID
    name: str
    category: str
    kind: str
    base_unit: str
    price_per_unit: Decimal | None
    # What one dose of this is measured in, per kg of feed: g, mL or pcs.
    dose_unit: str = ""
    tracked: bool
    active: bool
    notes: str | None
    # For a formula: the summed cost of its ingredients, filled in by the router.
    # Null when any ingredient has no price, so a partial total is never shown.
    cost_per_unit: Decimal | None = None
    units: list[ProductUnitOut] = Field(default_factory=list)
    components: list[ProductComponentOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def fill_dose_unit(self) -> "ProductOut":
        self.dose_unit = dose_unit_for(self.base_unit)
        return self


class ExpansionLine(BaseModel):
    """One stocked product a mixture resolves to, and what it would cost in stock."""

    product_id: uuid.UUID
    name: str
    amount: Decimal
    unit: str
    # Null when this warehouse holds no stock record for the product.
    in_stock: Decimal | None = None
    enough: bool = True


class ExpansionOut(BaseModel):
    product_id: uuid.UUID
    amount: Decimal
    unit: str
    lines: list[ExpansionLine]
