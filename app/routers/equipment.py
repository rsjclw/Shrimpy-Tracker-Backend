"""The equipment registry: reservoirs, bore wells, paddlewheels, pumps, generators, PLN meters.

Filled through this API by Claude and the farm agents; the dashboard only reads it.
Maintainers manage the registry itself; anyone who can add records can log where a
machine went and what happened to it.
"""

from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import Equipment, EquipmentEvent, EquipmentLink, EquipmentPlacement, Grid, Pond, Warehouse
from app.schemas import (
    EquipmentCreate,
    EquipmentDetail,
    EquipmentOut,
    EquipmentUpdate,
    EventIn,
    EventOut,
    LinkIn,
    LinkOut,
    PlacementIn,
    PlacementOut,
)
from app.services.access import accessible_farm_ids, require_farm_permission, require_grid_permission
from app.services.common import apply_updates, get_or_404
from app.services.equipment import current_placement, open_links, status_from_events

router = APIRouter(prefix="/equipment", tags=["equipment"])

_CODE_TAKEN = HTTPException(status.HTTP_409_CONFLICT, "That code is already used on this grid")


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail)


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _CODE_TAKEN


async def _equipment(db: AsyncSession, user: CurrentUser, equipment_id: UUID, permission: str) -> Equipment:
    eq = await get_or_404(db, Equipment, equipment_id, "Equipment not found")
    await require_grid_permission(db, user, eq.grid_id, permission)
    return eq


async def _on_grid(
    db: AsyncSession,
    eq: Equipment,
    *,
    ponds: tuple[UUID | None, ...] = (),
    equipment: tuple[UUID | None, ...] = (),
    warehouses: tuple[UUID | None, ...] = (),
    holder: UUID | None = None,
) -> None:
    """Everything a row points at must be on the machine's own grid; only a reservoir holds things."""
    for pond_id in filter(None, ponds):
        pond = await db.get(Pond, pond_id)
        if not pond or pond.grid_id != eq.grid_id:
            raise _unprocessable("That pond isn't on this equipment's grid")
    for other_id in filter(None, equipment):
        if other_id == eq.id:
            raise _unprocessable("Equipment can't point at itself")
        other = await db.get(Equipment, other_id)
        if not other or other.grid_id != eq.grid_id:
            raise _unprocessable("That equipment isn't on the same grid")
        if other_id == holder and other.kind != "reservoir":
            raise _unprocessable("Only a reservoir can hold equipment")
    for warehouse_id in filter(None, warehouses):
        warehouse = await db.get(Warehouse, warehouse_id)
        if not warehouse or warehouse.grid_id != eq.grid_id:
            raise _unprocessable("That warehouse isn't on this equipment's grid")


async def _children(db: AsyncSession, ids: list[UUID]):
    """Placements, links and events for these machines, grouped by machine, oldest first."""
    placements, links, events = defaultdict(list), defaultdict(list), defaultdict(list)
    if ids:
        for row in (await db.execute(
            select(EquipmentPlacement).where(EquipmentPlacement.equipment_id.in_(ids)).order_by(EquipmentPlacement.from_date)
        )).scalars():
            placements[row.equipment_id].append(row)
        for row in (await db.execute(
            select(EquipmentLink).where(EquipmentLink.equipment_id.in_(ids)).order_by(EquipmentLink.created_at)
        )).scalars():
            links[row.equipment_id].append(row)
        for row in (await db.execute(
            select(EquipmentEvent).where(EquipmentEvent.equipment_id.in_(ids)).order_by(EquipmentEvent.date, EquipmentEvent.created_at)
        )).scalars():
            events[row.equipment_id].append(row)
    return placements, links, events


def _summary(eq: Equipment, placements: list, links: list, events: list) -> EquipmentOut:
    now = current_placement(placements)
    return EquipmentOut(
        id=eq.id,
        grid_id=eq.grid_id,
        kind=eq.kind,
        code=eq.code,
        name=eq.name,
        specs=eq.specs or {},
        notes=eq.notes,
        status=status_from_events(events),
        placement=PlacementOut.model_validate(now) if now else None,
        links=[LinkOut.model_validate(link) for link in open_links(links)],
        # `events` comes oldest first (date, then entry time).
        last_event=EventOut.model_validate(events[-1]) if events else None,
    )


async def _detail(db: AsyncSession, eq: Equipment) -> EquipmentDetail:
    placements, links, events = await _children(db, [eq.id])
    p, l, e = placements[eq.id], links[eq.id], events[eq.id]
    return EquipmentDetail(
        **_summary(eq, p, l, e).model_dump(),
        placements=[PlacementOut.model_validate(row) for row in p],
        all_links=[LinkOut.model_validate(row) for row in l],
        events=[EventOut.model_validate(row) for row in e],
    )


# ---- The registry ----

@router.get("", response_model=list[EquipmentOut])
async def list_equipment(
    grid_id: UUID | None = None,
    farm_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[EquipmentOut]:
    stmt = select(Equipment).order_by(Equipment.kind, Equipment.code)
    if grid_id:
        await require_grid_permission(db, user, grid_id)
        stmt = stmt.where(Equipment.grid_id == grid_id)
    elif farm_id:
        await require_farm_permission(db, user, farm_id)
        stmt = stmt.join(Grid, Equipment.grid_id == Grid.id).where(Grid.farm_id == farm_id)
    else:
        farm_ids = await accessible_farm_ids(db, user)
        if not farm_ids:
            return []
        stmt = stmt.join(Grid, Equipment.grid_id == Grid.id).where(Grid.farm_id.in_(farm_ids))
    rows = list((await db.execute(stmt)).scalars().all())
    placements, links, events = await _children(db, [eq.id for eq in rows])
    return [_summary(eq, placements[eq.id], links[eq.id], events[eq.id]) for eq in rows]


@router.post("", response_model=EquipmentOut, status_code=status.HTTP_201_CREATED)
async def create_equipment(
    payload: EquipmentCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EquipmentOut:
    await require_grid_permission(db, user, payload.grid_id, "manage")
    eq = Equipment(**payload.model_dump())
    db.add(eq)
    await _commit(db)
    await db.refresh(eq)
    return _summary(eq, [], [], [])


@router.get("/{equipment_id}", response_model=EquipmentDetail)
async def get_equipment(
    equipment_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EquipmentDetail:
    return await _detail(db, await _equipment(db, user, equipment_id, "read"))


@router.put("/{equipment_id}", response_model=EquipmentDetail)
async def update_equipment(
    equipment_id: UUID,
    payload: EquipmentUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EquipmentDetail:
    eq = await _equipment(db, user, equipment_id, "manage")
    apply_updates(eq, payload, exclude_unset=False)
    await _commit(db)
    await db.refresh(eq)
    return await _detail(db, eq)


@router.delete("/{equipment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_equipment(
    equipment_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    eq = await _equipment(db, user, equipment_id, "manage")
    await db.delete(eq)
    await db.commit()


# ---- Where it sits ----

@router.post("/{equipment_id}/placements", response_model=PlacementOut, status_code=status.HTTP_201_CREATED)
async def add_placement(
    equipment_id: UUID,
    payload: PlacementIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EquipmentPlacement:
    """A new spot. Without to_date it is where the machine is now: the current spot ends
    where this one starts (or, on the same day, is replaced by it). With to_date it is a
    past spot, recorded as is."""
    eq = await _equipment(db, user, equipment_id, "add")
    await _on_grid(db, eq, ponds=(payload.pond_id,), equipment=(payload.in_equipment_id,),
                   warehouses=(payload.warehouse_id,), holder=payload.in_equipment_id)
    if payload.to_date is None:
        open_rows = (await db.execute(
            select(EquipmentPlacement).where(EquipmentPlacement.equipment_id == eq.id, EquipmentPlacement.to_date.is_(None))
        )).scalars().all()
        for row in open_rows:
            if row.from_date > payload.from_date:
                raise _unprocessable(f"{eq.code} has been somewhere else since {row.from_date}; a new spot can't start before that")
            if row.from_date == payload.from_date:
                await db.delete(row)
            else:
                row.to_date = payload.from_date
    placement = EquipmentPlacement(equipment_id=eq.id, **payload.model_dump())
    db.add(placement)
    await db.commit()
    await db.refresh(placement)
    return placement


@router.put("/placements/{placement_id}", response_model=PlacementOut)
async def fix_placement(
    placement_id: UUID,
    payload: PlacementIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EquipmentPlacement:
    """Correct a recorded spot in place (a wrong number, a wrong pond), adding no history."""
    placement = await get_or_404(db, EquipmentPlacement, placement_id, "Placement not found")
    eq = await _equipment(db, user, placement.equipment_id, "manage")
    await _on_grid(db, eq, ponds=(payload.pond_id,), equipment=(payload.in_equipment_id,),
                   warehouses=(payload.warehouse_id,), holder=payload.in_equipment_id)
    apply_updates(placement, payload, exclude_unset=False)
    await db.commit()
    await db.refresh(placement)
    return placement


@router.delete("/placements/{placement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_placement(
    placement_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    placement = await get_or_404(db, EquipmentPlacement, placement_id, "Placement not found")
    await _equipment(db, user, placement.equipment_id, "manage")
    await db.delete(placement)
    await db.commit()


# ---- What it moves water or power from and to ----

@router.post("/{equipment_id}/links", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
async def add_link(
    equipment_id: UUID,
    payload: LinkIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EquipmentLink:
    eq = await _equipment(db, user, equipment_id, "manage")
    await _on_grid(db, eq, ponds=(payload.from_pond_id, payload.to_pond_id),
                   equipment=(payload.from_equipment_id, payload.to_equipment_id))
    link = EquipmentLink(equipment_id=eq.id, **payload.model_dump())
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


@router.put("/links/{link_id}", response_model=LinkOut)
async def update_link(
    link_id: UUID,
    payload: LinkIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EquipmentLink:
    """Correct a link, or end it by setting to_date."""
    link = await get_or_404(db, EquipmentLink, link_id, "Link not found")
    eq = await _equipment(db, user, link.equipment_id, "manage")
    await _on_grid(db, eq, ponds=(payload.from_pond_id, payload.to_pond_id),
                   equipment=(payload.from_equipment_id, payload.to_equipment_id))
    apply_updates(link, payload, exclude_unset=False)
    await db.commit()
    await db.refresh(link)
    return link


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(
    link_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    link = await get_or_404(db, EquipmentLink, link_id, "Link not found")
    await _equipment(db, user, link.equipment_id, "manage")
    await db.delete(link)
    await db.commit()


# ---- What happened to it ----

@router.post("/{equipment_id}/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
async def add_event(
    equipment_id: UUID,
    payload: EventIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EquipmentEvent:
    eq = await _equipment(db, user, equipment_id, "add")
    event = EquipmentEvent(equipment_id=eq.id, created_by=user.email or user.id, **payload.model_dump())
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    event = await get_or_404(db, EquipmentEvent, event_id, "Event not found")
    await _equipment(db, user, event.equipment_id, "manage")
    await db.delete(event)
    await db.commit()
