"""Additives on a feeding: resolve them against the farm catalog and work out doses.

An additive is a catalog entry dosed into feed - a product straight off the shelf
(Vitagold) or a formula you make up (anti-WFD). There is no separate additive
list any more, and no stored default dose.

A feeding stores its additives as JSON entries
``{"product_id": str, "name": str, "dose_per_kg": "2.5", "dose_unit": "g"}``, per kg of feed and in
whatever that entry is dosed in - grams for a mass, millilitres for a volume.
Clients may send an entry by id or by name, with or without a dose. A missing
dose is taken from the last feeding in the same cycle that used it, so a dose
changed late in a cycle carries forward. There is no stored default.

Feedings written before the catalogs merged carry ``additive_id`` and
``dosage_gr_per_kg`` instead; those are matched by name so old cycles still read.
"""
from dataclasses import dataclass
from datetime import date as ddate
from datetime import time as dtime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Cycle, DailyLog, FeedingSession, Grid, Pond, Product
from app.schemas.feeding import FeedingAdditive
from app.schemas.units import dose_unit_for


@dataclass(frozen=True)
class LastDose:
    dosage_gr_per_kg: Decimal
    date: ddate
    feed_time: dtime


def _norm(name: str) -> str:
    return " ".join(name.split()).lower()


def _dose_text(value: Decimal) -> str:
    # Stored as text so JSON keeps the exact decimal ("2.5", not 2.4999...).
    return format(value.normalize(), "f")


def entry_dose(entry: dict) -> Decimal | None:
    """The dose on a stored entry, under either the current or the legacy key."""
    raw = entry.get("dose_per_kg", entry.get("dose_gr_per_kg", entry.get("dosage_gr_per_kg")))
    return None if raw is None else Decimal(str(raw))


async def farm_catalog(db: AsyncSession, farm_id: UUID) -> list[Product]:
    """Everything the farm can dose into feed: any active catalog entry."""
    result = await db.execute(
        select(Product).where(Product.farm_id == farm_id, Product.active.is_(True))
    )
    return list(result.scalars().all())


def _catalog_key(
    entry: dict, by_id: dict[str, Product], by_name: dict[str, Product]
) -> tuple[str | None, str]:
    """(catalog id, display name) of a stored entry.

    Entries from before the merge have no `product_id`, so they are matched on the
    name they were stored with.
    """
    product_id = str(entry.get("product_id") or "")
    if product_id in by_id:
        return product_id, by_id[product_id].name
    match = by_name.get(_norm(str(entry.get("name", ""))))
    if match:
        return str(match.id), match.name
    return None, str(entry.get("name", ""))


def _dose_rows(catalog: list[Product], rows) -> dict[str, LastDose]:
    """Walk feedings oldest first, so the last dose of each entry is what remains."""
    by_id = {str(p.id): p for p in catalog}
    by_name = {_norm(p.name): p for p in catalog}
    doses: dict[str, LastDose] = {}
    for log_date, feed_time, additives, _feeding_id in rows:
        for entry in additives or []:
            dose = entry_dose(entry)
            if dose is None:
                continue
            product_id, name = _catalog_key(entry, by_id, by_name)
            doses[product_id if product_id is not None else _norm(name)] = LastDose(
                dose, log_date, feed_time
            )
    return doses


def _feedings_stmt(*, farm_scope: bool):
    stmt = (
        select(DailyLog.date, FeedingSession.feed_time, FeedingSession.additives, FeedingSession.id)
        .join(DailyLog, DailyLog.id == FeedingSession.daily_log_id)
    )
    if farm_scope:
        stmt = (
            stmt.join(Cycle, Cycle.id == DailyLog.cycle_id)
            .join(Pond, Pond.id == Cycle.pond_id)
            .join(Grid, Grid.id == Pond.grid_id)
        )
    return stmt.order_by(DailyLog.date, FeedingSession.feed_time)


def _within(rows, before, exclude_feeding_id):
    for row in rows:
        log_date, feed_time, _additives, feeding_id = row
        if feeding_id == exclude_feeding_id:
            continue
        if before is not None and (log_date, feed_time) >= before:
            continue
        yield row


async def last_doses(
    db: AsyncSession,
    cycle_id: UUID,
    catalog: list[Product],
    *,
    before: tuple[ddate, dtime] | None = None,
    through_date: ddate | None = None,
    exclude_feeding_id: UUID | None = None,
) -> dict[str, LastDose]:
    """Most recent dose per entry in one cycle, keyed by catalog id (or name when unmatched).

    `before` limits to feedings strictly earlier than that date and time;
    `through_date` limits to feedings on or before that date.
    """
    stmt = _feedings_stmt(farm_scope=False).where(DailyLog.cycle_id == cycle_id)
    if through_date is not None:
        stmt = stmt.where(DailyLog.date <= through_date)
    rows = (await db.execute(stmt)).all()
    return _dose_rows(catalog, _within(rows, before, exclude_feeding_id))


async def farm_last_doses(
    db: AsyncSession,
    farm_id: UUID,
    catalog: list[Product],
    *,
    before: tuple[ddate, dtime] | None = None,
    through_date: ddate | None = None,
    exclude_feeding_id: UUID | None = None,
) -> dict[str, LastDose]:
    """The same, across every cycle on the farm.

    What a cycle is doing wins, but a cycle that has not dosed something yet is
    better served by what the farm last did than by nothing at all - otherwise
    every dose is retyped on day one of every cycle.
    """
    stmt = _feedings_stmt(farm_scope=True).where(Grid.farm_id == farm_id)
    if through_date is not None:
        stmt = stmt.where(DailyLog.date <= through_date)
    rows = (await db.execute(stmt)).all()
    return _dose_rows(catalog, _within(rows, before, exclude_feeding_id))


async def resolve_additives(
    db: AsyncSession,
    *,
    farm_id: UUID,
    cycle_id: UUID,
    entries: list[FeedingAdditive],
    log_date: ddate,
    feed_time: dtime,
    feeding_id: UUID | None = None,
) -> list[dict]:
    """Turn client entries into stored entries, or raise 400 with a message a client can act on."""
    if not entries:
        return []
    catalog = await farm_catalog(db, farm_id)
    by_id = {str(p.id): p for p in catalog}
    by_name = {_norm(p.name): p for p in catalog}
    history: dict[str, LastDose] | None = None
    farm_history: dict[str, LastDose] | None = None
    resolved: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.product_id is not None:
            product = by_id.get(str(entry.product_id))
            if product is None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{entry.product_id} is not in this farm's catalog",
                )
        else:
            product = by_name.get(_norm(entry.name or ""))
            if product is None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Unknown additive '{entry.name}'. Add it to the farm's catalog first.",
                )
        key = str(product.id)
        if key in seen:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{product.name} is listed twice on this feeding")
        seen.add(key)

        dose = entry.dose_per_kg
        if dose is None:
            if history is None:
                history = await last_doses(
                    db, cycle_id, catalog, before=(log_date, feed_time), exclude_feeding_id=feeding_id
                )
            last = history.get(key)
            if last is None:
                # Nothing in this cycle yet, so fall back to what the farm last did.
                if farm_history is None:
                    farm_history = await farm_last_doses(
                        db, farm_id, catalog, before=(log_date, feed_time), exclude_feeding_id=feeding_id
                    )
                last = farm_history.get(key)
            dose = last.dosage_gr_per_kg if last else None
        if dose is None:
            unit = dose_unit_for(product.base_unit)
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"No dose for {product.name}: send dose_per_kg ({unit} per kg of feed). "
                "Nothing on this farm has used it yet.",
            )
        resolved.append(
            {
                "product_id": key,
                "name": product.name,
                "dose_per_kg": _dose_text(Decimal(dose)),
                # Snapshotted like the feed price is: what the dose meant when logged.
                "dose_unit": dose_unit_for(product.base_unit),
            }
        )
    return resolved


async def current_doses(db: AsyncSession, farm_id: UUID, cycle_id: UUID, as_of: ddate) -> list[dict]:
    """Per catalog entry: the dose a feeding on `as_of` gets when it leaves the dose out.

    What this cycle last dosed, falling back to what the farm last did so a new
    cycle does not start by retyping every dose. There is no stored default.
    """
    catalog = await farm_catalog(db, farm_id)
    history = await farm_last_doses(db, farm_id, catalog, through_date=as_of)
    # The cycle's own doses win wherever it has any.
    history.update(await last_doses(db, cycle_id, catalog, through_date=as_of))
    out = []
    for product in sorted(catalog, key=lambda p: p.name.lower()):
        key = str(product.id)
        last = history.get(key)
        if last is None:
            continue
        out.append(
            {
                "product_id": key,
                "name": product.name,
                "dose_per_kg": last.dosage_gr_per_kg,
                "dose_unit": dose_unit_for(product.base_unit),
                "last_used_date": last.date,
            }
        )
    return out


async def usage_by_day(db: AsyncSession, farm_id: UUID, cycle_id: UUID, date_from: ddate, date_to: ddate) -> list[dict]:
    """Per day and entry: feed that carried it, grams given, and the day's average dose."""
    catalog = await farm_catalog(db, farm_id)
    by_id = {str(p.id): p for p in catalog}
    by_name = {_norm(p.name): p for p in catalog}
    rows = (
        await db.execute(
            select(DailyLog.date, FeedingSession.amount_kg, FeedingSession.additives)
            .join(DailyLog, DailyLog.id == FeedingSession.daily_log_id)
            .where(DailyLog.cycle_id == cycle_id, DailyLog.date >= date_from, DailyLog.date <= date_to)
        )
    ).all()
    totals: dict[tuple[ddate, str], dict] = {}
    for log_date, amount_kg, additives in rows:
        for entry in additives or []:
            dose = entry_dose(entry)
            if dose is None:
                continue
            product_id, name = _catalog_key(entry, by_id, by_name)
            key = (log_date, product_id if product_id is not None else _norm(name))
            row = totals.setdefault(
                key,
                {
                    "date": log_date,
                    "product_id": product_id,
                    "name": name,
                    "feed_kg": Decimal(0),
                    "amount_g": Decimal(0),
                },
            )
            row["feed_kg"] += Decimal(amount_kg)
            row["amount_g"] += Decimal(amount_kg) * dose
    out = []
    for row in sorted(totals.values(), key=lambda r: (r["date"], r["name"].lower())):
        feed = row["feed_kg"]
        out.append(
            {
                **row,
                "dosage_gr_per_kg": (row["amount_g"] / feed) if feed else Decimal(0),
            }
        )
    return out
