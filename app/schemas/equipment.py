import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EquipmentKind = Literal["reservoir", "bore_well", "paddlewheel", "pump", "generator", "pln_meter", "blower"]
EquipmentEventKind = Literal[
    "broken", "sent_for_repair", "repaired", "serviced", "part_replaced", "disinfected", "note", "retired"
]
EquipmentStatus = Literal["working", "broken", "in_repair", "retired"]


def _one_or_none(*values: Any) -> int:
    return sum(v is not None for v in values)


class EquipmentFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # The owner's label, e.g. "A0-K1"; unique per grid, renamable.
    code: str = Field(min_length=1, max_length=40)
    name: str | None = Field(default=None, max_length=100)
    # Per kind, free-form: {"hp": 1, "wheels": 2}, {"kva": 15, "fuel": "diesel"}, {"width_m": 25, "length_m": 18},
    # a blower's {"type": "ring" | "roots", "hp": 3}.
    specs: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class EquipmentCreate(EquipmentFields):
    grid_id: uuid.UUID
    kind: EquipmentKind


class EquipmentUpdate(EquipmentFields):
    """Everything but grid and kind, which are fixed. PUT replaces every field."""


class PlacementIn(BaseModel):
    """In a pond, in a reservoir, in a warehouse, or (none of them) on the grid itself."""

    pond_id: uuid.UUID | None = None
    in_equipment_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    # In a pond or reservoir: metres from its own west and north sides. On the grid:
    # the machine's middle on the layout drawing, which may lie west or north of 0.
    x_m: Decimal | None = Field(default=None, max_digits=8, decimal_places=2)
    y_m: Decimal | None = Field(default=None, max_digits=8, decimal_places=2)
    # In a pond or reservoir: which way a paddlewheel pushes water, from that pond's own
    # north side (0) clockwise. On the grid: how far it is turned (a reservoir).
    bearing: int | None = Field(default=None, ge=0, le=359)
    from_date: date
    # Leave empty for where it is now; set it to record a past spot.
    to_date: date | None = None
    note: str | None = None

    @model_validator(mode="after")
    def one_place_and_dates_in_order(self) -> "PlacementIn":
        if _one_or_none(self.pond_id, self.in_equipment_id, self.warehouse_id) > 1:
            raise ValueError("Give at most one of pond_id, in_equipment_id, warehouse_id")
        inside = self.pond_id is not None or self.in_equipment_id is not None
        if inside and any(v is not None and v < 0 for v in (self.x_m, self.y_m)):
            raise ValueError("Inside a pond or reservoir, x_m and y_m are measured from its west and north sides: 0 or more")
        if self.to_date and self.to_date < self.from_date:
            raise ValueError("to_date is before from_date")
        return self


class LinkIn(BaseModel):
    """From a pond, a piece of equipment, or nothing; to exactly one pond or piece of equipment."""

    from_pond_id: uuid.UUID | None = None
    from_equipment_id: uuid.UUID | None = None
    to_pond_id: uuid.UUID | None = None
    to_equipment_id: uuid.UUID | None = None
    from_date: date | None = None
    to_date: date | None = None
    note: str | None = None

    @model_validator(mode="after")
    def ends_and_dates(self) -> "LinkIn":
        if _one_or_none(self.from_pond_id, self.from_equipment_id) > 1:
            raise ValueError("Give at most one of from_pond_id, from_equipment_id")
        if _one_or_none(self.to_pond_id, self.to_equipment_id) != 1:
            raise ValueError("Give exactly one of to_pond_id, to_equipment_id")
        if self.from_date and self.to_date and self.to_date < self.from_date:
            raise ValueError("to_date is before from_date")
        return self


class EventIn(BaseModel):
    date: date
    kind: EquipmentEventKind
    note: str | None = None
    cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    run_hours: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=1)


class PlacementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pond_id: uuid.UUID | None
    in_equipment_id: uuid.UUID | None
    warehouse_id: uuid.UUID | None
    x_m: Decimal | None
    y_m: Decimal | None
    bearing: int | None
    from_date: date
    to_date: date | None
    note: str | None


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_pond_id: uuid.UUID | None
    from_equipment_id: uuid.UUID | None
    to_pond_id: uuid.UUID | None
    to_equipment_id: uuid.UUID | None
    from_date: date | None
    to_date: date | None
    note: str | None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    date: date
    kind: str
    note: str | None
    cost: Decimal | None
    run_hours: Decimal | None
    created_by: str | None
    created_at: datetime


class EquipmentOut(BaseModel):
    """One piece of equipment as it is now: status, where it sits, what it feeds."""

    id: uuid.UUID
    grid_id: uuid.UUID
    kind: str
    code: str
    name: str | None
    specs: dict[str, Any]
    notes: str | None
    status: EquipmentStatus
    placement: PlacementOut | None
    links: list[LinkOut]
    # The latest history entry (by date, then entry time), for lists.
    last_event: EventOut | None = None


class EquipmentDetail(EquipmentOut):
    """Plus everything that came before: every placement, link and event."""

    placements: list[PlacementOut]
    all_links: list[LinkOut]
    events: list[EventOut]
