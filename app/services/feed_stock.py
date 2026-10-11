"""Feed off the shelf: a feeding takes its feed and additives out of its pond's warehouse.

Feedings are written from many places - the day editor, the forecast, blind
feeding, batch imports, the farm agents through the API - and some of those
delete them in bulk, so moving stock route by route would miss some. Instead
`sync` reconciles: it works out what every feeding should have taken, compares
that with what the ledger says it took (the movements tagged with it), and writes
only the difference. A second run writes nothing. Feeding writes call it, and a
sweep runs it every few minutes, which is also how a forecast feeding starts
counting once its day comes.

What a feeding takes:
- nothing dated before `settings.feed_stock_from`, nor before its day has come on
  the farm's clock: a forecast writes days ahead, and those are plans. Nor after
  an ended cycle's last day, where leftover forecast rows feed an empty pond;
- its feed by the mix it was fed: its own `feed_types`, else the feed of the
  feeding before it in the cycle, else the farm's first feed product - the rule
  the dashboard shows, so stock moves by what the screen says was fed;
- its additives, dose per kg x kg of feed, in each product's own unit;
- formulas expand into the products they are made of; untracked entries drop out.

Where from: the pond's warehouse. A pond with none moves no stock, and what its
feedings took before is left as it was. A feeding whose amounts have not changed
stays where it was taken even if its pond now draws elsewhere, so reassigning a
pond does not move its history; one that changed is settled against the pond's
current warehouse. A warehouse with no stock row for the product gets one at 0:
the balance going negative is the signal to do a stock count.

A deleted feeding gives back everything it took. So does a treatment removed
without its own DELETE (deleting the cycle, or a forecast applied over its day).
"""
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from itertools import groupby
from uuid import UUID

from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal
from app.models import (
    Cycle,
    DailyLog,
    FeedingSession,
    Grid,
    InventoryItem,
    InventoryMovement,
    Pond,
    Product,
    Treatment,
)
from app.schemas.units import base_units_per_dose_unit, dose_unit_for
from app.services.additives import entry_dose
from app.services.clock import farm_today
from app.services.inventory import apply_movement, reverse_source
from app.services.metrics import cycle_end_date
from app.services.products import Catalog, ProductError, expand, load_catalog

logger = logging.getLogger(__name__)

SOURCE = "feeding"
TICK_SECONDS = 300
# Every sync takes this lock, so two never read the same ledger and both write
# the same difference. Any number works as long as it is this one.
_LOCK_KEY = 0x5EEDF00D
_STEP = Decimal("0.001")
# Feed is weighed in kg; a product counted in either of these needs no unit row.
_PER_KG = {"kg": Decimal("1"), "g": Decimal("1000")}


@dataclass(frozen=True)
class Taken:
    """What the ledger says one feeding has out of one stock row, net of corrections."""

    item_id: UUID
    product_id: UUID
    amount: Decimal


def _norm(name: str) -> str:
    return " ".join(name.split()).lower()


def carried_mixes(own_mixes: list[list[dict]], fallback: list[dict]) -> list[list[dict]]:
    """What each of a cycle's feedings was fed, oldest first.

    A feeding without a mix of its own was fed the one before it; the first ones
    fall back to `fallback` until a mix turns up.
    """
    running = fallback
    out = []
    for own in own_mixes:
        if own:
            running = own
        out.append(running)
    return out


def entry_product(catalog: Catalog, by_name: dict[str, Product], entry: dict) -> Product | None:
    """The catalog entry a stored feed or additive line means: by id, else by name.

    Lines from before the catalogs merged carry `feed_type_id` (or `additive_id`)
    and a brand and type instead of a name, and the prediction engine once wrote
    ids that are not UUIDs at all.
    """
    for key in ("product_id", "feed_type_id", "additive_id"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            product = catalog.products.get(UUID(str(raw)))
        except ValueError:
            continue
        if product is not None:
            return product
    name = entry.get("name") or " ".join(str(part) for part in (entry.get("brand"), entry.get("type")) if part)
    return by_name.get(_norm(name)) if name else None


def _per_kg(catalog: Catalog, product: Product) -> Decimal | None:
    """Base units in one kg of feed, or None when the product cannot be weighed."""
    factor = _PER_KG.get(product.base_unit.strip())
    if factor is not None:
        return factor
    try:
        return catalog.factor(product, "kg")
    except ProductError:
        return None


def _per_dose_unit(catalog: Catalog, product: Product, dose_unit: str | None) -> Decimal | None:
    """Base units in one dose unit (1 g of something counted in kg is 0.001), or None if it does not convert."""
    natural = dose_unit_for(product.base_unit)
    unit = (dose_unit or "").strip() or natural
    if unit == natural:
        return base_units_per_dose_unit(product.base_unit)
    try:
        return catalog.factor(product, unit)
    except ProductError:
        return None


def wanted(
    catalog: Catalog,
    by_name: dict[str, Product],
    amount_kg: Decimal,
    mix: list[dict],
    additives: list[dict],
) -> dict[UUID, Decimal]:
    """What one feeding takes: {stocked product: amount in its base unit}.

    Lines naming nothing in the catalog, or in a unit that does not convert, take
    nothing: there is no stock row they could honestly come out of. Raises
    ProductError for a recipe that contains itself.
    """
    lines: list[tuple[UUID, Decimal]] = []
    for entry in mix:
        product = entry_product(catalog, by_name, entry)
        share = Decimal(str(entry.get("percentage") or 0)) / 100
        factor = _per_kg(catalog, product) if product is not None else None
        if factor is not None:
            lines.append((product.id, amount_kg * share * factor))
    for entry in additives:
        product = entry_product(catalog, by_name, entry)
        dose = entry_dose(entry)
        factor = _per_dose_unit(catalog, product, entry.get("dose_unit")) if product is not None else None
        if factor is not None and dose is not None:
            lines.append((product.id, amount_kg * dose * factor))
    totals = expand(catalog, [(pid, amount) for pid, amount in lines if amount > 0])
    rounded = {pid: qty.quantize(_STEP, rounding=ROUND_HALF_UP) for pid, qty in totals.items()}
    return {pid: qty for pid, qty in rounded.items() if qty > 0}


def unchanged(taken: list[Taken], want: dict[UUID, Decimal]) -> bool:
    """Whether a feeding already has out exactly what it should, wherever it came from."""
    have: dict[UUID, Decimal] = defaultdict(Decimal)
    for t in taken:
        have[t.product_id] += t.amount
    return {pid: qty for pid, qty in have.items() if qty} == want


def differences(taken: dict[UUID, Decimal], target: dict[UUID, Decimal]) -> dict[UUID, Decimal]:
    """{stock row: how much more to take out} to go from taken to target. Negative puts back."""
    out = {}
    for item_id in taken.keys() | target.keys():
        diff = target.get(item_id, Decimal("0")) - taken.get(item_id, Decimal("0"))
        if diff:
            out[item_id] = diff
    return out


def first_feed(catalog: Catalog) -> list[dict]:
    """The farm's first feed product at 100%, the mix of a cycle that has none yet.

    The same pick as day_view._farm_default_feed_types, which is what the
    dashboard shows on those feedings.
    """
    feeds = [p for p in catalog.products.values() if p.category == "feed" and p.active]
    if not feeds:
        return []
    first = min(feeds, key=lambda p: (p.created_at, p.name))
    return [{"product_id": str(first.id), "name": first.name, "percentage": 100}]


def _label(pond_name: str, day, feed_time) -> str:
    return f"{pond_name} {day.day} {day:%b} {feed_time:%H:%M}"


async def _give_back_orphans(db: AsyncSession, actor: str | None) -> int:
    """Put back what deleted feedings and treatments still have out. Returns how many."""
    count = 0
    for source_type, model, note in (
        (SOURCE, FeedingSession, "Feeding deleted"),
        ("treatment", Treatment, "Treatment deleted"),
    ):
        result = await db.execute(
            select(InventoryMovement.source_id)
            .where(
                InventoryMovement.source_type == source_type,
                InventoryMovement.source_id.is_not(None),
                ~exists().where(model.id == InventoryMovement.source_id),
            )
            .group_by(InventoryMovement.source_id, InventoryMovement.item_id)
            .having(func.sum(InventoryMovement.delta) != 0)
        )
        for source_id in sorted({row[0] for row in result.all()}):
            await reverse_source(db, source_type, source_id, note=note, created_by=actor)
            count += 1
    return count


async def _taken(db: AsyncSession) -> dict[UUID, list[Taken]]:
    result = await db.execute(
        select(
            InventoryMovement.source_id,
            InventoryMovement.item_id,
            InventoryItem.product_id,
            func.sum(InventoryMovement.delta),
        )
        .join(InventoryItem, InventoryItem.id == InventoryMovement.item_id)
        .where(InventoryMovement.source_type == SOURCE)
        .group_by(InventoryMovement.source_id, InventoryMovement.item_id, InventoryItem.product_id)
    )
    taken: dict[UUID, list[Taken]] = defaultdict(list)
    for source_id, item_id, product_id, net in result.all():
        if net:
            taken[source_id].append(Taken(item_id, product_id, -net))
    return taken


async def sync(db: AsyncSession, *, actor: str | None = None) -> int:
    """Bring the ledger in line with every feeding. Returns how many feedings (and
    deleted things) it settled. Does not commit: the caller owns the transaction."""
    if not settings.feed_stock_enabled:
        return 0
    await db.execute(text(f"SELECT pg_advisory_xact_lock({_LOCK_KEY})"))
    settled = await _give_back_orphans(db, actor)

    start, today = settings.feed_stock_from, farm_today()
    if start > today:
        return settled
    # Whole cycles, back to their first feeding: a feeding in the window may be
    # following a feed set long before it.
    cycles = (
        select(DailyLog.cycle_id)
        .join(FeedingSession, FeedingSession.daily_log_id == DailyLog.id)
        .join(Cycle, Cycle.id == DailyLog.cycle_id)
        .join(Pond, Pond.id == Cycle.pond_id)
        .where(DailyLog.date >= start, DailyLog.date <= today, Pond.warehouse_id.is_not(None))
        .distinct()
    )
    result = await db.execute(
        select(
            FeedingSession.id,
            FeedingSession.amount_kg,
            FeedingSession.feed_types,
            FeedingSession.additives,
            FeedingSession.feed_time,
            FeedingSession.updated_by,
            DailyLog.date,
            DailyLog.cycle_id,
            Cycle.status,
            Cycle.actual_end_date,
            Cycle.planned_end_date,
            Pond.name,
            Pond.warehouse_id,
            Grid.farm_id,
        )
        .join(DailyLog, DailyLog.id == FeedingSession.daily_log_id)
        .join(Cycle, Cycle.id == DailyLog.cycle_id)
        .join(Pond, Pond.id == Cycle.pond_id)
        .join(Grid, Grid.id == Pond.grid_id)
        .where(DailyLog.cycle_id.in_(cycles), DailyLog.date <= today)
        .order_by(DailyLog.cycle_id, DailyLog.date, FeedingSession.feed_time, FeedingSession.id)
    )
    rows = result.all()
    if not rows:
        return settled

    taken = await _taken(db)
    catalogs: dict[UUID, tuple[Catalog, dict[str, Product], list[dict]]] = {}
    pending = []  # (row, what it has out, what it should have out)
    for _cycle_id, group in groupby(rows, key=lambda r: r.cycle_id):
        group = list(group)
        farm_id = group[0].farm_id
        if farm_id not in catalogs:
            catalog = await load_catalog(db, farm_id)
            by_name = {_norm(p.name): p for p in catalog.products.values()}
            catalogs[farm_id] = (catalog, by_name, first_feed(catalog))
        catalog, by_name, fallback = catalogs[farm_id]
        # The row carries the cycle's status and dates, which is all this reads.
        end = cycle_end_date(group[0], today)
        for row, mix in zip(group, carried_mixes([r.feed_types or [] for r in group], fallback)):
            if row.warehouse_id is None:
                continue
            counts = row.date >= start and (end is None or row.date <= end)
            try:
                want = wanted(catalog, by_name, Decimal(row.amount_kg), mix, row.additives or []) if counts else {}
            except ProductError as exc:
                logger.warning("Feeding %s moves no stock: %s", row.id, exc)
                continue
            have = taken.get(row.id, [])
            if not unchanged(have, want):
                pending.append((row, have, want))
    if not pending:
        return settled

    # The stock rows each changed feeding settles against, made where missing.
    needed = {(row.warehouse_id, pid) for row, _have, want in pending for pid in want}
    items: dict[tuple[UUID, UUID], UUID] = {}
    if needed:
        found = await db.execute(
            select(InventoryItem.warehouse_id, InventoryItem.product_id, InventoryItem.id).where(
                InventoryItem.warehouse_id.in_({w for w, _p in needed}),
                InventoryItem.product_id.in_({p for _w, p in needed}),
            )
        )
        items = {(w, p): item_id for w, p, item_id in found.all()}
        made = [
            InventoryItem(warehouse_id=warehouse_id, product_id=product_id, quantity=Decimal("0"))
            for warehouse_id, product_id in sorted(needed - items.keys())
        ]
        if made:
            db.add_all(made)
            await db.flush()  # the ids come from the insert
            items.update({(item.warehouse_id, item.product_id): item.id for item in made})

    plans = []
    for row, have, want in pending:
        target = {items[(row.warehouse_id, pid)]: qty for pid, qty in want.items()}
        diffs = differences({t.item_id: t.amount for t in have}, target)
        prefix = "Feeding changed" if have else "Feeding"
        plans.append((row, f"{prefix} {_label(row.name, row.date, row.feed_time)}", diffs))

    # Locked in id order, as everything else that moves stock does, so nothing deadlocks.
    locked = await db.execute(
        select(InventoryItem)
        .where(InventoryItem.id.in_({item_id for _r, _n, diffs in plans for item_id in diffs}))
        .order_by(InventoryItem.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    stock = {item.id: item for item in locked.scalars()}
    for row, note, diffs in plans:
        for item_id in sorted(diffs):
            diff = diffs[item_id]
            item = stock[item_id]
            kind = "use" if diff > 0 else "receive"
            delta, new = apply_movement(item.quantity, kind, abs(diff))
            item.quantity = new
            db.add(
                InventoryMovement(
                    item_id=item_id,
                    kind=kind,
                    delta=delta,
                    quantity_after=new,
                    note=note,
                    source_type=SOURCE,
                    source_id=row.id,
                    created_by=row.updated_by or actor,
                )
            )
    await db.flush()
    return settled + len(plans)


async def sync_quietly(db: AsyncSession, *, actor: str | None = None) -> None:
    """`sync` for a route that has just written feedings, inside a savepoint.

    What the user logged matters more than the stock it moves: if the sync fails
    it is rolled back on its own, the write still commits, and the sweep tries
    again within minutes.
    """
    try:
        async with db.begin_nested():
            await sync(db, actor=actor)
    except Exception:
        logger.exception("Feed stock sync failed; the sweep will retry")


async def run_forever() -> None:
    while True:
        try:
            async with SessionLocal() as db:
                settled = await sync(db)
                await db.commit()
            if settled:
                logger.info("Feed stock sweep settled %d feedings", settled)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed sweep must not kill the loop - try again next tick.
            logger.exception("Feed stock sweep failed")
        await asyncio.sleep(TICK_SECONDS)
