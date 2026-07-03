from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.water import WaterParametersOut, WaterParametersUpsert


def _water_out(**kwargs):
    return WaterParametersOut(id=uuid4(), daily_log_id=uuid4(), **kwargs)


def test_biology_counts_accept_non_negative_values_and_nulls():
    payload = WaterParametersUpsert(
        plankton_ga=Decimal("10"),
        yellow_vibrio=Decimal("0"),
        tbc=None,
    )

    assert payload.plankton_ga == Decimal("10")
    assert payload.yellow_vibrio == Decimal("0")
    assert payload.tbc is None


def test_biology_counts_reject_negative_values():
    with pytest.raises(ValidationError):
        WaterParametersUpsert(plankton_ga=Decimal("-1"))


def test_water_computed_totals_are_null_when_source_groups_are_empty():
    out = _water_out(tbc=Decimal("100"))

    assert out.total_plankton is None
    assert out.total_vibrio_count is None
    assert out.vibrio_percentage is None


def test_water_computed_totals_treat_partial_source_groups_as_zero_filled():
    out = _water_out(
        plankton_ga=Decimal("10"),
        plankton_bga=None,
        plankton_diatom=Decimal("5"),
        yellow_vibrio=Decimal("1"),
        green_vibrio=None,
        black_vibrio=Decimal("2"),
        tbc=Decimal("12"),
    )

    assert out.total_plankton == Decimal("15")
    assert out.total_vibrio_count == Decimal("3")
    assert out.vibrio_percentage == Decimal("25.00")


def test_vibrio_percentage_is_null_when_tbc_is_zero():
    out = _water_out(
        yellow_vibrio=Decimal("1"),
        green_vibrio=Decimal("2"),
        black_vibrio=Decimal("3"),
        tbc=Decimal("0"),
    )

    assert out.total_vibrio_count == Decimal("6")
    assert out.vibrio_percentage is None
