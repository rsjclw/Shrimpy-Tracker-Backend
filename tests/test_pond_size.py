from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import PondCreate, PondUpdate

GRID = "11111111-1111-1111-1111-111111111111"


def test_width_and_length_set_the_area():
    p = PondUpdate(name="0", width_m=Decimal("30"), length_m=Decimal("30"))
    assert p.area_m2 == Decimal("900.00")


def test_an_area_sent_with_width_and_length_is_replaced_by_their_product():
    p = PondCreate(grid_id=GRID, name="1", area_m2=Decimal("1"), width_m=Decimal("50"), length_m=Decimal("30.5"))
    assert p.area_m2 == Decimal("1525.00")


def test_a_pond_without_a_size_keeps_working_with_just_an_area():
    p = PondUpdate(name="0", area_m2=Decimal("900"))
    assert (p.area_m2, p.width_m, p.length_m) == (Decimal("900"), None, None)
    assert PondUpdate(name="0").area_m2 is None


def test_width_without_length_is_refused():
    with pytest.raises(ValidationError):
        PondUpdate(name="0", width_m=Decimal("30"))
    with pytest.raises(ValidationError):
        PondCreate(grid_id=GRID, name="0", length_m=Decimal("30"), area_m2=Decimal("900"))


def test_sizes_must_be_positive():
    with pytest.raises(ValidationError):
        PondUpdate(name="0", width_m=Decimal("0"), length_m=Decimal("30"))
    with pytest.raises(ValidationError):
        PondUpdate(name="0", width_m=Decimal("-5"), length_m=Decimal("30"))
