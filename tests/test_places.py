from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models import Place
from app.schemas import PlaceCreate, PlaceOut, PlaceUpdate

GRID = "11111111-1111-1111-1111-111111111111"


def test_a_reservoir_takes_width_and_length():
    r = PlaceCreate(grid_id=GRID, kind="reservoir", name="  Reservoir 0 ", width_m=Decimal("25"), length_m=Decimal("18"))
    assert (r.name, r.width_m, r.length_m) == ("Reservoir 0", Decimal("25"), Decimal("18"))
    assert PlaceCreate(grid_id=GRID, kind="reservoir", name="R1").width_m is None  # not measured yet


def test_width_and_length_come_together_and_positive():
    with pytest.raises(ValidationError):
        PlaceCreate(grid_id=GRID, kind="reservoir", name="R", width_m=Decimal("25"))
    with pytest.raises(ValidationError):
        PlaceUpdate(name="R", length_m=Decimal("18"))
    with pytest.raises(ValidationError):
        PlaceUpdate(name="R", width_m=Decimal("0"), length_m=Decimal("18"))


def test_a_bore_well_has_no_size():
    assert PlaceCreate(grid_id=GRID, kind="bore_well", name="Bore well A").width_m is None
    with pytest.raises(ValidationError):
        PlaceCreate(grid_id=GRID, kind="bore_well", name="Bore well A", width_m=Decimal("1"), length_m=Decimal("1"))


def test_only_reservoirs_and_bore_wells():
    with pytest.raises(ValidationError):
        PlaceCreate(grid_id=GRID, kind="pond", name="P")
    with pytest.raises(ValidationError):
        PlaceCreate(grid_id=GRID, kind="reservoir", name="   ")


def test_area_is_width_times_length():
    p = Place(grid_id=GRID, kind="reservoir", name="R", width_m=Decimal("25.5"), length_m=Decimal("18"))
    assert p.area_m2 == Decimal("459.00")
    assert Place(grid_id=GRID, kind="bore_well", name="W").area_m2 is None


def test_out_carries_the_area():
    row = SimpleNamespace(id="22222222-2222-2222-2222-222222222222", grid_id=GRID, kind="reservoir", name="R",
                          width_m=Decimal("25"), length_m=Decimal("18"), area_m2=Decimal("450.00"), notes=None)
    assert PlaceOut.model_validate(row).area_m2 == Decimal("450.00")
