import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlaceSize(BaseModel):
    """Width and length in metres, both or neither (an unmeasured reservoir has neither)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    width_m: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)
    length_m: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)
    notes: str | None = None

    @model_validator(mode="after")
    def both_or_neither(self) -> "PlaceSize":
        if (self.width_m is None) != (self.length_m is None):
            raise ValueError("Give both width_m and length_m, or neither")
        return self


class PlaceCreate(PlaceSize):
    grid_id: uuid.UUID
    kind: Literal["reservoir", "bore_well"]

    @model_validator(mode="after")
    def a_well_has_no_size(self) -> "PlaceCreate":
        if self.kind == "bore_well" and self.width_m is not None:
            raise ValueError("A bore well has no width or length")
        return self


class PlaceUpdate(PlaceSize):
    """Everything but the kind, which is fixed once the place exists. PUT replaces every field."""


class PlaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    grid_id: uuid.UUID
    kind: str
    name: str
    width_m: Decimal | None
    length_m: Decimal | None
    # width_m x length_m; null until both are known.
    area_m2: Decimal | None
    notes: str | None
