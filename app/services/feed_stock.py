"""Feed off the shelf: a feeding takes its feed and additives out of its pond's warehouse.

Feedings are written from many places - the day editor, the forecast, blind
feeding, batch imports, the farm agents through the API - and some of those
delete them in bulk, so moving stock route by route would miss some. Instead
`sync` reconciles: it works out what every feeding should have taken, compares
that with what the ledger says it took (the movements tagged with it), and writes
only the difference. A second run writes nothing. Feeding writes call it, and a
sweep runs it every few minutes, which is also how a planned feeding starts
counting once its feed time comes.

What a feeding takes:
- nothing dated before `settings.feed_stock_from`, nor before its feed time has
  come on the farm's clock: the day's feeds are written ahead (the morning plan,
  a forecast days out), and until their time they are plans. Nor after an ended
  cycle's last day, where leftover forecast rows feed an empty pond;
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
from datetime import date as ddate
from datetime import datetime
from datetime import time as dtime
from decimal import ROUND_HALF_UP, Decimal
from itertools import groupby
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, or_, select, text
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
from app.services.clock import farm_now
from app.services.inventory import apply_movement
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
    return _by_product(taken) == want


def _by_product(taken: list[Taken]) -> dict[UUID, Decimal]:
    have: dict[UUID, Decimal] = defaultdict(Decimal)
    for t in taken:
        have[t.product_id] += t.amount
    return {pid: qty for pid, qty in have.items() if qty}


def settled_by_count(
    taken: list[Taken],
    want: dict[UUID, Decimal],
    fed_at: datetime,
    counted_at: dict[UUID, datetime],
    pond_rows: dict[UUID, UUID],
) -> set[UUID]:
    """Products whose stock a count taken after this feeding already settled.

    A stock count is what was on the shelf when it was taken, so a feed given
    before it is in it, recorded right or not. From then on that product no
    longer moves for this feeding, whatever is edited on it. `pond_rows` is
    {product: its stock row in the pond's warehouse}.
    """
    held = {t.product_id for t in taken if (c := counted_at.get(t.item_id)) is not None and c > fed_at}
    for pid in want:
        row = pond_rows.get(pid)
        if row is not None and (c := counted_at.get(row)) is not None and c > fed_at:
            held.add(pid)
    return held


def goal(taken: list[Taken], want: dict[UUID, Decimal], held: set[UUID]) -> dict[UUID, Decimal]:
    """What a feeding should have out per product: what it wants, except held products stay as they are."""
    have = _by_product(taken)
    out = {pid: qty for pid, qty in want.items() if pid not in held}
    out.update({pid: have[pid] for pid in held if pid in have})
    return out


def targets(
    taken: list[Taken], want: dict[UUID, Decimal], held: set[UUID], pond_rows: dict[UUID, UUID]
) -> dict[UUID, Decimal]:
    """{stock row: what this feeding should have out of it}. Held products keep their rows; the
    rest come out of the pond's warehouse, so rows elsewhere go back to nothing."""
    target = {t.item_id: t.amount for t in taken if t.product_id in held}
    for pid, qty in want.items():
        if pid not in held:
            target[pond_rows[pid]] = qty
    return target


def differences(taken: dict[UUID, Decimal], target: dict[UUID, Decimal]) -> dict[UUID, Decimal]:
    """{stock row: how much more to take out} to go from taken to target. Negative puts back."""
    out = {}
    for item_id in taken.keys() | target.keys():
        diff = target.get(item_id, Decimal("0")) - taken.get(item_id, Decimal("0"))
        if diff:
            out[item_id] = diff
    return out


def counts(day: ddate, feed_time: dtime, start: ddate, end: ddate | None, now: datetime) -> bool:
    """Whether a feeding takes stock yet: from `start`, once its feed time has come, up to the cycle's end."""
    return start <= day and datetime.combine(day, feed_time) <= now and (end is None or day <= end)


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


def _last_counts():
    """Each stock row's latest count. Movements before it are inside the counted amount."""
    return (
        select(InventoryMovement.item_id.label("item_id"), func.max(InventoryMovement.created_at).label("at"))
        .where(InventoryMovement.kind == "count")
        .group_by(InventoryMovement.item_id)
        .subquery()
    )


async def _lock(db: AsyncSession, item_ids: set[UUID]) -> dict[UUID, InventoryItem]:
    """Stock rows locked in id order, as everything else that moves stock does, so nothing deadlocks."""
    result = await db.execute(
        select(InventoryItem)
        .where(InventoryItem.id.in_(item_ids))
        .order_by(InventoryItem.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return {item.id: item for item in result.scalars()}


def _move(db: AsyncSession, item: InventoryItem, amount: Decimal, **fields) -> None:
    """Take `amount` out of a row (negative puts it back) and record it."""
    kind = "use" if amount > 0 else "receive"
    delta, new = apply_movement(item.quantity, kind, abs(amount))
    item.quantity = new
    # The time of the write, not of the transaction: it is compared with stock
    # counts, and this may run in a transaction that began before one was taken.
    db.add(
        InventoryMovement(
            item_id=item.id, kind=kind, delta=delta, quantity_after=new, created_at=func.clock_timestamp(), **fields
        )
    )


async def _give_back_orphans(db: AsyncSession, actor: str | None) -> int:
    """Put back what deleted feedings and treatments still have out since each row's last count.
    Returns how many."""
    last = _last_counts()
    moves = []
    for source_type, model, note in (
        (SOURCE, FeedingSession, "Feeding deleted"),
        ("treatment", Treatment, "Treatment deleted"),
    ):
        result = await db.execute(
            select(InventoryMovement.source_id, InventoryMovement.item_id, func.sum(InventoryMovement.delta))
            .outerjoin(last, last.c.item_id == InventoryMovement.item_id)
            .where(
                InventoryMovement.source_type == source_type,
                InventoryMovement.source_id.is_not(None),
                ~exists().where(model.id == InventoryMovement.source_id),
                or_(last.c.at.is_(None), InventoryMovement.created_at > last.c.at),
            )
            .group_by(InventoryMovement.source_id, InventoryMovement.item_id)
            .having(func.sum(InventoryMovement.delta) != 0)
        )
        moves += [(source_type, source_id, item_id, net, note) for source_id, item_id, net in result.all()]
    if not moves:
        return 0
    stock = await _lock(db, {item_id for _t, _s, item_id, _n, _note in moves})
    for source_type, source_id, item_id, net, note in sorted(moves, key=lambda m: (m[2], m[1])):
        # A negative net is stock still out, so putting it back is a negative take.
        _move(db, stock[item_id], net, note=note, source_type=source_type, source_id=source_id, created_by=actor)
    return len({(m[0], m[1]) for m in moves})


async def _taken(db: AsyncSession) -> dict[UUID, list[Taken]]:
    """What each feeding has out, per stock row, since that row was last counted."""
    last = _last_counts()
    result = await db.execute(
        select(
            InventoryMovement.source_id,
            InventoryMovement.item_id,
            InventoryItem.product_id,
            func.sum(InventoryMovement.delta),
        )
        .join(InventoryItem, InventoryItem.id == InventoryMovement.item_id)
        .outerjoin(last, last.c.item_id == InventoryMovement.item_id)
        .where(
            InventoryMovement.source_type == SOURCE,
            or_(last.c.at.is_(None), InventoryMovement.created_at > last.c.at),
        )
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

    start, now = settings.feed_stock_from, farm_now()
    today = now.date()
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
    counted = await db.execute(select(_last_counts()))
    counted_at: dict[UUID, datetime] = dict(counted.all())
    found = await db.execute(
        select(InventoryItem.warehouse_id, InventoryItem.product_id, InventoryItem.id).where(
            InventoryItem.warehouse_id.in_({r.warehouse_id for r in rows if r.warehouse_id is not None})
        )
    )
    items: dict[tuple[UUID, UUID], UUID] = {(w, p): item_id for w, p, item_id in found.all()}
    zone = ZoneInfo(settings.default_timezone)

    catalogs: dict[UUID, tuple[Catalog, dict[str, Product], list[dict]]] = {}
    pending = []  # (row, what it has out, what it wants, products a count has settled)
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
            try:
                want = (
                    wanted(catalog, by_name, Decimal(row.amount_kg), mix, row.additives or [])
                    if counts(row.date, row.feed_time, start, end, now)
                    else {}
                )
            except ProductError as exc:
                logger.warning("Feeding %s moves no stock: %s", row.id, exc)
                continue
            have = taken.get(row.id, [])
            pond_rows = {pid: items[(row.warehouse_id, pid)] for pid in want if (row.warehouse_id, pid) in items}
            fed_at = datetime.combine(row.date, row.feed_time, tzinfo=zone)
            held = settled_by_count(have, want, fed_at, counted_at, pond_rows)
            if not unchanged(have, goal(have, want, held)):
                pending.append((row, have, want, held))
    if not pending:
        return settled

    # A warehouse with no stock row for a product it is now giving out gets one at 0.
    missing = sorted(
        {(row.warehouse_id, pid) for row, _have, want, held in pending for pid in want if pid not in held}
        - items.keys()
    )
    if missing:
        made = [InventoryItem(warehouse_id=w, product_id=p, quantity=Decimal("0")) for w, p in missing]
        db.add_all(made)
        await db.flush()  # the ids come from the insert
        items.update({(item.warehouse_id, item.product_id): item.id for item in made})

    plans = []
    for row, have, want, held in pending:
        pond_rows = {pid: items[(row.warehouse_id, pid)] for pid in want if pid not in held}
        diffs = differences({t.item_id: t.amount for t in have}, targets(have, want, held, pond_rows))
        if not have:
            prefix = "Feeding"
        elif datetime.combine(row.date, row.feed_time) > now:
            prefix = "Feeding not due yet"  # its time was moved later, or it was taken early
        else:
            prefix = "Feeding changed"
        plans.append((row, f"{prefix} {_label(row.name, row.date, row.feed_time)}", diffs))

    stock = await _lock(db, {item_id for _r, _n, diffs in plans for item_id in diffs})
    for row, note, diffs in plans:
        for item_id in sorted(diffs):
            _move(
                db,
                stock[item_id],
                diffs[item_id],
                note=note,
                source_type=SOURCE,
                source_id=row.id,
                created_by=row.updated_by or actor,
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
