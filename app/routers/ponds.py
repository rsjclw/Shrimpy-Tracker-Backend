from fastapi import APIRouter, Depends, status
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import Cycle, Grid, Pond
from app.schemas import CycleOut, PondCreate, PondOut, PondUpdate
from app.services.access import (
    accessible_farm_ids,
    require_farm_permission,
    require_grid_permission,
    require_pond_permission,
)
from app.services.common import apply_updates, get_or_404

router = APIRouter(prefix="/ponds", tags=["ponds"])


@router.get("", response_model=list[PondOut])
async def list_ponds(
    grid_id: str | None = None,
    farm_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[Pond]:
    stmt = select(Pond).order_by(Pond.name)
    if grid_id:
        await require_grid_permission(db, user, grid_id)
        stmt = stmt.where(Pond.grid_id == grid_id)
    elif farm_id:
        await require_farm_permission(db, user, farm_id)
        stmt = stmt.join(Grid, Pond.grid_id == Grid.id).where(Grid.farm_id == farm_id)
    else:
        farm_ids = await accessible_farm_ids(db, user)
        if not farm_ids:
            return []
        stmt = stmt.join(Grid, Pond.grid_id == Grid.id).where(Grid.farm_id.in_(farm_ids))
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=PondOut, status_code=status.HTTP_201_CREATED)
async def create_pond(
    payload: PondCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Pond:
    await require_grid_permission(db, user, payload.grid_id, "add")
    pond = Pond(**payload.model_dump())
    db.add(pond)
    await db.commit()
    await db.refresh(pond)
    return pond


@router.get("/{pond_id}", response_model=PondOut)
async def get_pond(
    pond_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Pond:
    await require_pond_permission(db, user, pond_id)
    return await get_or_404(db, Pond, pond_id, "Pond not found")


@router.put("/{pond_id}", response_model=PondOut)
async def update_pond(
    pond_id: str,
    payload: PondUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Pond:
    await require_pond_permission(db, user, pond_id, "manage")
    pond = await get_or_404(db, Pond, pond_id, "Pond not found")
    apply_updates(pond, payload, exclude_unset=False)
    await db.commit()
    await db.refresh(pond)
    return pond


@router.delete("/{pond_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pond(
    pond_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    await require_pond_permission(db, user, pond_id, "manage")
    pond = await get_or_404(db, Pond, pond_id, "Pond not found")
    await db.delete(pond)
    await db.commit()


@router.get("/{pond_id}/cycles", response_model=list[CycleOut])
async def list_pond_cycles(
    pond_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[Cycle]:
    await require_pond_permission(db, user, pond_id)
    result = await db.execute(
        select(Cycle).where(Cycle.pond_id == pond_id).order_by(Cycle.start_date.desc())
    )
    return list(result.scalars().all())
