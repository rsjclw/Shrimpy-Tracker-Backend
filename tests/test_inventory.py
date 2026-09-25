import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import InventoryItemCreate, MovementCreate
from app.services.inventory import MovementError, apply_movement


def test_receive_adds_to_stock():
    assert apply_movement(Decimal("10"), "receive", Decimal("2.5")) == (Decimal("2.500"), Decimal("12.500"))


def test_use_subtracts_from_stock():
    assert apply_movement(Decimal("10"), "use", Decimal("4")) == (Decimal("-4.000"), Decimal("6.000"))


def test_use_can_empty_stock():
    assert apply_movement(Decimal("3"), "use", Decimal("3"))[1] == Decimal("0")


def test_use_may_go_negative_rather_than_lose_what_was_really_used():
    """A negative balance says the receiving or counting is behind, not that the
    worker should be stopped from recording what they applied."""
    assert apply_movement(Decimal("3"), "use", Decimal("5")) == (Decimal("-5.000"), Decimal("-2.000"))
    assert apply_movement(Decimal("0"), "use", Decimal("2"))[1] == Decimal("-2.000")


def test_a_stock_count_brings_a_negative_balance_back():
    assert apply_movement(Decimal("-2"), "count", Decimal("40")) == (Decimal("42.000"), Decimal("40.000"))


def test_count_sets_the_exact_amount_and_records_the_difference():
    assert apply_movement(Decimal("10"), "count", Decimal("7")) == (Decimal("-3.000"), Decimal("7.000"))
    assert apply_movement(Decimal("10"), "count", Decimal("0")) == (Decimal("-10.000"), Decimal("0.000"))


@pytest.mark.parametrize("kind", ["receive", "use"])
def test_receive_and_use_need_a_positive_amount(kind):
    with pytest.raises(MovementError):
        apply_movement(Decimal("10"), kind, Decimal("0"))


def test_unknown_kind_is_rejected():
    with pytest.raises(MovementError):
        apply_movement(Decimal("10"), "transfer", Decimal("1"))


def test_movement_rejects_negative_amounts_and_unknown_kinds():
    with pytest.raises(ValidationError):
        MovementCreate(kind="use", amount=Decimal("-1"))
    with pytest.raises(ValidationError):
        MovementCreate(kind="steal", amount=Decimal("1"))


def test_a_stock_row_only_says_which_item_and_how_much():
    """Name, category and unit live on the product now, so they cannot be sent here."""
    item = InventoryItemCreate(product_id=uuid.uuid4())
    assert item.quantity == Decimal("0")
    assert not hasattr(item, "name") and not hasattr(item, "unit") and not hasattr(item, "category")


def test_a_stock_row_needs_an_item():
    with pytest.raises(ValidationError):
        InventoryItemCreate()


def test_opening_stock_cannot_be_negative():
    with pytest.raises(ValidationError):
        InventoryItemCreate(product_id=uuid.uuid4(), quantity=Decimal("-1"))
