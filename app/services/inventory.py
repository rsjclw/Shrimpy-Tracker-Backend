"""Stock arithmetic, and taking several products out of a warehouse at once."""
from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InventoryItem, InventoryMovement, Product

_STEP = Decimal("0.001")


class MovementError(ValueError):
    pass


def apply_movement(current: Decimal, kind: str, amount: Decimal) -> tuple[Decimal, Decimal]:
    """Return (delta, new quantity) for a movement.

    receive adds, use subtracts, count sets the exact amount found on a stock take.

    Use may take the balance below zero. What a worker actually fed or applied is
    the fact worth keeping; a balance that disagrees means the receiving or the
    counting is behind, and refusing the entry would lose the fact and fix nothing.
    A negative balance is the signal to go and do a stock count.
    """
    amount = amount.quantize(_STEP)
    if kind == "count":
        new = amount
    elif kind in ("receive", "use"):
        if amount <= 0:
            raise MovementError("Enter an amount above 0")
        new = current + amount if kind == "receive" else current - amount
    else:
        raise MovementError(f"Unknown movement kind {kind!r}")
    return new - current, new


def delivery_amount(amount: Decimal | None, pack_count: Decimal | None, pack_size: Decimal | None) -> Decimal:
    """The base amount a receive adds: as sent, or `pack_count` packs of `pack_size`.

    With both, they must agree to the gram (0.001), so a typo in either is caught
    instead of silently picking one.
    """
    if pack_count is None:
        if amount is None:
            raise MovementError("Enter an amount, or how many packs came")
        return amount
    if pack_size is None:
        raise MovementError("Say how big one pack is")
    packed = (pack_count * pack_size).quantize(_STEP)
    if amount is not None and amount.quantize(_STEP) != packed:
        raise MovementError(f"{pack_count} packs of {pack_size} is {packed}, not {amount}")
    return packed


def unit_cost(total_cost: Decimal | None, amount: Decimal | None) -> Decimal | None:
    """What one base unit of a delivery cost, to the rupiah cent; None without a cost or an amount."""
    if total_cost is None or not amount or amount <= 0:
        return None
    return (total_cost / amount).quantize(Decimal("0.01"))


async def _lock_items_for_products(
    db: AsyncSession, warehouse_id: UUID, product_ids: list[UUID]
) -> dict[UUID, InventoryItem]:
    """The stock rows for these products in this warehouse, locked for update.

    Locking in a fixed id order matters: two treatments touching the same pair of
    items in opposite orders would otherwise deadlock.
    """
    if not product_ids:
        return {}
    result = await db.execute(
        select(InventoryItem)
        .where(InventoryItem.warehouse_id == warehouse_id, InventoryItem.product_id.in_(product_ids))
        .order_by(InventoryItem.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return {item.product_id: item for item in result.scalars()}


async def consume_products(
    db: AsyncSession,
    warehouse_id: UUID,
    totals: dict[UUID, Decimal],
    *,
    source_type: str,
    source_id: UUID,
    note: str | None = None,
    created_by: str | None = None,
) -> None:
    """Take several products out of one warehouse as a single all-or-nothing step.

    `totals` is {product id: amount in that product's base unit}, already expanded
    from any mixtures. Does not commit: the caller owns the transaction, so a
    treatment and the stock it used are written together or not at all.
    """
    if not totals:
        return
    items = await _lock_items_for_products(db, warehouse_id, list(totals))
    missing = [pid for pid in totals if pid not in items]
    if missing:
        names = await db.execute(select(Product.name).where(Product.id.in_(missing)).order_by(Product.name))
        listed = ", ".join(names.scalars())
        raise MovementError(f"This warehouse has no stock record for: {listed}")

    for product_id in sorted(totals, key=lambda pid: items[pid].id):
        item = items[product_id]
        delta, new = apply_movement(item.quantity, "use", totals[product_id])
        item.quantity = new
        db.add(
            InventoryMovement(
                item_id=item.id,
                kind="use",
                delta=delta,
                quantity_after=new,
                note=note,
                source_type=source_type,
                source_id=source_id,
                created_by=created_by,
            )
        )


async def reverse_source(
    db: AsyncSession,
    source_type: str,
    source_id: UUID,
    *,
    note: str | None = None,
    created_by: str | None = None,
) -> None:
    """Put back everything a treatment (or anything else) took, per item.

    Works off the *net* of every movement already tagged with this source, so
    running it twice is harmless: after the first pass the net is zero and the
    second finds nothing to do. Does not commit.
    """
    result = await db.execute(
        select(InventoryMovement.item_id, func.sum(InventoryMovement.delta))
        .where(InventoryMovement.source_type == source_type, InventoryMovement.source_id == source_id)
        .group_by(InventoryMovement.item_id)
    )
    net: dict[UUID, Decimal] = {item_id: total for item_id, total in result.all() if total}
    if not net:
        return

    locked = await db.execute(
        select(InventoryItem)
        .where(InventoryItem.id.in_(list(net)))
        .order_by(InventoryItem.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    for item in locked.scalars():
        amount = net[item.id]
        # The original took stock out (negative net), so putting it back is a receive.
        kind = "receive" if amount < 0 else "use"
        delta, new = apply_movement(item.quantity, kind, abs(amount))
        item.quantity = new
        db.add(
            InventoryMovement(
                item_id=item.id,
                kind=kind,
                delta=delta,
                quantity_after=new,
                note=note,
                source_type=source_type,
                source_id=source_id,
                created_by=created_by,
            )
        )
