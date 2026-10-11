"""Reservoirs and bore wells: a grid's water that isn't a pond."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import Grid, Place
from app.schemas import PlaceCreate, PlaceOut, PlaceUpdate
from app.services.access import accessible_farm_ids, require_farm_permission, require_grid_permission
from app.services.common import apply_updates, get_or_404

router = APIRouter(prefix="/places", tags=["places"])

_CONFLICT = HTTPException(status.HTTP_409_CONFLICT, "A reservoir or well with that name already exists on this grid")


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _CONFLICT


@router.get("", response_model=list[PlaceOut])
async def list_places(
    grid_id: UUID | None = None,
    farm_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[Place]:
    # Reservoirs before wells, then by name.
    stmt = select(Place).order_by(Place.kind.desc(), Place.name)
    if grid_id:
        await require_grid_permission(db, user, grid_id)
        stmt = stmt.where(Place.grid_id == grid_id)
    elif farm_id:
        await require_farm_permission(db, user, farm_id)
        stmt = stmt.join(Grid, Place.grid_id == Grid.id).where(Grid.farm_id == farm_id)
    else:
        farm_ids = await accessible_farm_ids(db, user)
        if not farm_ids:
            return []
        stmt = stmt.join(Grid, Place.grid_id == Grid.id).where(Grid.farm_id.in_(farm_ids))
    return list((await db.execute(stmt)).scalars().all())


@router.post("", response_model=PlaceOut, status_code=status.HTTP_201_CREATED)
async def create_place(
    payload: PlaceCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Place:
    await require_grid_permission(db, user, payload.grid_id, "manage")
    place = Place(**payload.model_dump())
    db.add(place)
    await _commit(db)
    await db.refresh(place)
    return place


@router.get("/{place_id}", response_model=PlaceOut)
async def get_place(
    place_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Place:
    place = await get_or_404(db, Place, place_id, "Place not found")
    await require_grid_permission(db, user, place.grid_id)
    return place


@router.put("/{place_id}", response_model=PlaceOut)
async def update_place(
    place_id: UUID,
    payload: PlaceUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Place:
    place = await get_or_404(db, Place, place_id, "Place not found")
    await require_grid_permission(db, user, place.grid_id, "manage")
    if place.kind == "bore_well" and payload.width_m is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A bore well has no width or length")
    apply_updates(place, payload, exclude_unset=False)
    await _commit(db)
    await db.refresh(place)
    return place


@router.delete("/{place_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_place(
    place_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    place = await get_or_404(db, Place, place_id, "Place not found")
    await require_grid_permission(db, user, place.grid_id, "manage")
    await db.delete(place)
    await db.commit()
