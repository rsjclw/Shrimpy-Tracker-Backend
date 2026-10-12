from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import PlacementIn, PondLayout, PondUpdate

POND = "22222222-2222-2222-2222-222222222222"
RES = "33333333-3333-3333-3333-333333333333"
D = date(2026, 10, 12)


def test_a_pond_is_drawn_at_its_middle_and_turned_clockwise():
    p = PondLayout(x_m=Decimal("40"), y_m=Decimal("-12.5"), rotation=25)
    assert (p.x_m, p.y_m, p.rotation) == (Decimal("40"), Decimal("-12.5"), 25)
    assert PondLayout().x_m is None and PondLayout().rotation == 0  # taken off the drawing


def test_a_pond_layout_needs_both_coordinates_and_a_whole_turn():
    with pytest.raises(ValidationError):
        PondLayout(x_m=Decimal("40"))
    with pytest.raises(ValidationError):
        PondLayout(x_m=Decimal("1"), y_m=Decimal("1"), rotation=360)
    with pytest.raises(ValidationError):
        PondLayout(x_m=Decimal("1"), y_m=Decimal("1"), rotation=-1)


def test_the_pond_update_does_not_carry_the_layout():
    # PUT /ponds/{id} replaces every field it knows; the layout must not be one of them.
    assert not {"x_m", "y_m", "rotation"} & set(PondUpdate.model_fields)


def test_on_the_grid_a_spot_may_lie_west_or_north_of_the_origin():
    p = PlacementIn(x_m=Decimal("-3"), y_m=Decimal("-8.5"), bearing=30, from_date=D)
    assert (p.x_m, p.y_m, p.bearing) == (Decimal("-3"), Decimal("-8.5"), 30)


def test_inside_a_pond_or_reservoir_a_spot_is_measured_from_its_sides():
    assert PlacementIn(pond_id=POND, x_m=Decimal("0"), y_m=Decimal("4"), from_date=D).x_m == 0
    with pytest.raises(ValidationError):
        PlacementIn(pond_id=POND, x_m=Decimal("-1"), y_m=Decimal("4"), from_date=D)
    with pytest.raises(ValidationError):
        PlacementIn(in_equipment_id=RES, x_m=Decimal("2"), y_m=Decimal("-0.5"), from_date=D)
