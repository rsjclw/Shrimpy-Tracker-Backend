"""Feeding additives: resolve them against the farm catalog and work out doses.

A feeding stores its additives as JSON entries
``{"additive_id": int | None, "name": str, "dosage_gr_per_kg": "2.5"}``.
Clients may send an additive by catalog id or by name, with or without a dose.
A missing dose is taken from the last feeding in the same cycle that used the
additive (so a dose changed late in a cycle carries forward), then from the
catalog's default dose.
"""
from dataclasses import dataclass
from datetime import date as ddate
from datetime import time as dtime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyLog, FeedAdditive, FeedingSession
from app.schemas.feeding import FeedingAdditive


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


async def farm_catalog(db: AsyncSession, farm_id: UUID) -> list[FeedAdditive]:
    result = await db.execute(select(FeedAdditive).where(FeedAdditive.farm_id == farm_id))
    return list(result.scalars().all())


def _catalog_key(entry: dict, by_id: dict[int, FeedAdditive], by_name: dict[str, FeedAdditive]) -> tuple[int | None, str]:
    """(catalog id, display name) of a stored entry; legacy entries are matched by name."""
    additive_id = entry.get("additive_id")
    if additive_id in by_id:
        return additive_id, by_id[additive_id].name
    match = by_name.get(_norm(str(entry.get("name", ""))))
    if match:
        return match.id, match.name
    return None, str(entry.get("name", ""))


async def last_doses(
    db: AsyncSession,
    cycle_id: UUID,
    catalog: list[FeedAdditive],
    *,
    before: tuple[ddate, dtime] | None = None,
    through_date: ddate | None = None,
    exclude_feeding_id: UUID | None = None,
) -> dict[int | str, LastDose]:
    """Most recent dose per additive in a cycle, keyed by catalog id (or name for unmatched legacy entries).

    `before` limits to feedings strictly earlier than that date and time;
    `through_date` limits to feedings on or before that date.
    """
    stmt = (
        select(DailyLog.date, FeedingSession.feed_time, FeedingSession.additives, FeedingSession.id)
        .join(DailyLog, DailyLog.id == FeedingSession.daily_log_id)
        .where(DailyLog.cycle_id == cycle_id)
        .order_by(DailyLog.date, FeedingSession.feed_time)
    )
    if through_date is not None:
        stmt = stmt.where(DailyLog.date <= through_date)
    rows = (await db.execute(stmt)).all()
    by_id = {a.id: a for a in catalog}
    by_name = {_norm(a.name): a for a in catalog}
    doses: dict[int | str, LastDose] = {}
    for log_date, feed_time, additives, feeding_id in rows:
        if feeding_id == exclude_feeding_id:
            continue
        if before is not None and (log_date, feed_time) >= before:
            continue
        for entry in additives or []:
            if entry.get("dosage_gr_per_kg") is None:
                continue
            additive_id, name = _catalog_key(entry, by_id, by_name)
            doses[additive_id if additive_id is not None else _norm(name)] = LastDose(
                Decimal(str(entry["dosage_gr_per_kg"])), log_date, feed_time
            )
    return doses


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
    by_id = {a.id: a for a in catalog}
    by_name = {_norm(a.name): a for a in catalog}
    history: dict[int | str, LastDose] | None = None
    resolved: list[dict] = []
    seen: set[int] = set()
    for entry in entries:
        if entry.additive_id is not None:
            additive = by_id.get(entry.additive_id)
            if additive is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Additive id {entry.additive_id} is not in this farm's additive list")
        else:
            additive = by_name.get(_norm(entry.name or ""))
            if additive is None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Unknown additive '{entry.name}'. Add it to the farm's additive list first.",
                )
        if additive.id in seen:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{additive.name} is listed twice on this feeding")
        seen.add(additive.id)

        dose = entry.dosage_gr_per_kg
        if dose is None:
            if history is None:
                history = await last_doses(db, cycle_id, catalog, before=(log_date, feed_time), exclude_feeding_id=feeding_id)
            last = history.get(additive.id)
            dose = last.dosage_gr_per_kg if last else additive.dosage_gr_per_kg
        if dose is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"No dose for {additive.name}: send dosage_gr_per_kg, or set a default dose in the farm's additive list.",
            )
        resolved.append({"additive_id": additive.id, "name": additive.name, "dosage_gr_per_kg": _dose_text(Decimal(dose))})
    return resolved


async def current_doses(db: AsyncSession, farm_id: UUID, cycle_id: UUID, as_of: ddate) -> list[dict]:
    """Per catalog additive: the dose a feeding on `as_of` gets when it leaves the dose out."""
    catalog = await farm_catalog(db, farm_id)
    history = await last_doses(db, cycle_id, catalog, through_date=as_of)
    out = []
    for additive in sorted(catalog, key=lambda a: a.name.lower()):
        last = history.get(additive.id)
        if last:
            dose, source, used = last.dosage_gr_per_kg, "last_used", last.date
        elif additive.dosage_gr_per_kg is not None:
            dose, source, used = additive.dosage_gr_per_kg, "default", None
        else:
            dose, source, used = None, "none", None
        out.append(
            {
                "additive_id": additive.id,
                "name": additive.name,
                "dosage_gr_per_kg": dose,
                "source": source,
                "last_used_date": used,
                "default_dosage_gr_per_kg": additive.dosage_gr_per_kg,
            }
        )
    return out


async def usage_by_day(db: AsyncSession, farm_id: UUID, cycle_id: UUID, date_from: ddate, date_to: ddate) -> list[dict]:
    """Per day and additive: feed that carried it, grams given, and the day's average dose."""
    catalog = await farm_catalog(db, farm_id)
    by_id = {a.id: a for a in catalog}
    by_name = {_norm(a.name): a for a in catalog}
    rows = (
        await db.execute(
            select(DailyLog.date, FeedingSession.amount_kg, FeedingSession.additives)
            .join(DailyLog, DailyLog.id == FeedingSession.daily_log_id)
            .where(DailyLog.cycle_id == cycle_id, DailyLog.date >= date_from, DailyLog.date <= date_to)
        )
    ).all()
    totals: dict[tuple[ddate, int | str], dict] = {}
    for log_date, amount_kg, additives in rows:
        for entry in additives or []:
            if entry.get("dosage_gr_per_kg") is None:
                continue
            additive_id, name = _catalog_key(entry, by_id, by_name)
            key = (log_date, additive_id if additive_id is not None else _norm(name))
            row = totals.setdefault(key, {"date": log_date, "additive_id": additive_id, "name": name, "feed_kg": Decimal(0), "amount_g": Decimal(0)})
            row["feed_kg"] += Decimal(amount_kg)
            row["amount_g"] += Decimal(amount_kg) * Decimal(str(entry["dosage_gr_per_kg"]))
    out = []
    for row in sorted(totals.values(), key=lambda r: (r["date"], r["name"].lower())):
        feed = row["feed_kg"]
        out.append(
            {
                **row,
                "feed_kg": feed.quantize(Decimal("0.001")),
                "amount_g": row["amount_g"].quantize(Decimal("0.1")),
                "dosage_gr_per_kg": (row["amount_g"] / feed).quantize(Decimal("0.001")) if feed else Decimal(0),
            }
        )
    return out
