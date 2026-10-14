import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import Product, ProductUnit
from app.routers.inventory import _pack_size
from app.schemas import InventoryItemCreate, MovementCreate, MovementOut, MovementUpdate
from app.services.inventory import MovementError, apply_movement, delivery_amount, unit_cost


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


# Deliveries: how they came and what they cost belong to the receive.


def _product(base="kg", **units):
    return Product(name="Omyacarb", base_unit=base,
                   units=[ProductUnit(unit=u, factor_to_base=Decimal(str(f))) for u, f in units.items()])


def test_a_delivery_in_sacks_is_the_sacks_times_their_size():
    assert delivery_amount(None, Decimal("26"), Decimal("25")) == Decimal("650.000")


def test_an_amount_sent_with_packs_must_agree():
    assert delivery_amount(Decimal("650"), Decimal("26"), Decimal("25")) == Decimal("650.000")
    with pytest.raises(MovementError):
        delivery_amount(Decimal("600"), Decimal("26"), Decimal("25"))


def test_a_plain_amount_needs_no_packs():
    assert delivery_amount(Decimal("12.5"), None, None) == Decimal("12.5")
    with pytest.raises(MovementError):
        delivery_amount(None, None, None)


def test_the_pack_size_defaults_to_the_products_own_unit():
    product = _product(sack=40)
    assert _pack_size(product, "sack", None) == Decimal("40")
    assert _pack_size(product, "Sack", None) == Decimal("40")
    # A delivery that came in a different bag says so.
    assert _pack_size(product, "sack", Decimal("50")) == Decimal("50")
    assert _pack_size(product, "kg", None) == Decimal("1")
    assert _pack_size(product, None, None) is None


def test_an_unknown_pack_without_a_size_is_refused():
    with pytest.raises(MovementError, match="send pack_size"):
        _pack_size(_product(sack=40), "jerrycan", None)


def test_a_delivery_costs_its_total_over_its_amount():
    assert unit_cost(Decimal("11960000"), Decimal("650")) == Decimal("18400.00")
    assert unit_cost(None, Decimal("650")) is None
    assert unit_cost(Decimal("100"), Decimal("0")) is None


def test_delivery_details_belong_to_a_receive():
    MovementCreate(kind="receive", pack_unit="sack", pack_count=Decimal("26"), total_cost=Decimal("11960000"),
                   supplier="Mas Congwi")
    for kind in ("use", "count"):
        with pytest.raises(ValidationError):
            MovementCreate(kind=kind, amount=Decimal("5"), total_cost=Decimal("1000"))
        with pytest.raises(ValidationError):
            MovementCreate(kind=kind, amount=Decimal("5"), supplier="Mas Congwi")


def test_packs_come_as_unit_and_count_together():
    with pytest.raises(ValidationError):
        MovementCreate(kind="receive", pack_unit="sack", amount=Decimal("50"))
    with pytest.raises(ValidationError):
        MovementCreate(kind="receive", pack_count=Decimal("2"), amount=Decimal("50"))
    with pytest.raises(ValidationError):
        MovementCreate(kind="receive", amount=Decimal("50"), pack_size=Decimal("25"))
    with pytest.raises(ValidationError):
        MovementCreate(kind="receive")


def test_a_cost_filled_in_later_needs_no_amount():
    update = MovementUpdate(total_cost=Decimal("11960000"))
    assert update.model_dump(exclude_unset=True) == {"total_cost": Decimal("11960000")}


def test_a_received_movement_shows_what_one_unit_cost():
    base = dict(id=uuid.uuid4(), item_id=uuid.uuid4(), quantity_after=Decimal("746.1"), note=None,
                created_by="claude", created_at="2026-10-14T10:00:00+07:00")
    received = MovementOut(kind="receive", delta=Decimal("650"), total_cost=Decimal("11960000"), **base)
    assert received.unit_cost == Decimal("18400.00")
    used = MovementOut(kind="use", delta=Decimal("-5"), **base)
    assert used.unit_cost is None and used.total_cost is None
