from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import Grid, Pond
from app.schemas import GridCreate, GridOut, GridUpdate, PondOut
from app.services.access import accessible_farm_ids, require_farm_permission, require_grid_permission
from app.services.common import apply_updates, get_or_404

router = APIRouter(prefix="/grids", tags=["grids"])


@router.get("", response_model=list[GridOut])
async def list_grids(
    farm_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[Grid]:
    if farm_id:
        await require_farm_permission(db, user, farm_id)
        stmt = select(Grid).where(Grid.farm_id == farm_id)
    else:
        farm_ids = await accessible_farm_ids(db, user)
        if not farm_ids:
            return []
        stmt = select(Grid).where(Grid.farm_id.in_(farm_ids))
    result = await db.execute(stmt.order_by(Grid.created_at))
    return list(result.scalars().all())


@router.post("", response_model=GridOut, status_code=status.HTTP_201_CREATED)
async def create_grid(
    payload: GridCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Grid:
    await require_farm_permission(db, user, payload.farm_id, "add")
    grid = Grid(**payload.model_dump())
    db.add(grid)
    await db.commit()
    await db.refresh(grid)
    return grid


@router.get("/{grid_id}/ponds", response_model=list[PondOut])
async def list_grid_ponds(
    grid_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[Pond]:
    await require_grid_permission(db, user, grid_id)
    result = await db.execute(
        select(Pond).where(Pond.grid_id == grid_id).order_by(Pond.name)
    )
    return list(result.scalars().all())


@router.get("/{grid_id}", response_model=GridOut)
async def get_grid(
    grid_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Grid:
    await require_grid_permission(db, user, grid_id)
    return await get_or_404(db, Grid, grid_id, "Grid not found")


@router.put("/{grid_id}", response_model=GridOut)
async def update_grid(
    grid_id: str,
    payload: GridUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Grid:
    await require_grid_permission(db, user, grid_id, "manage")
    grid = await get_or_404(db, Grid, grid_id, "Grid not found")
    apply_updates(grid, payload, exclude_unset=False)
    await db.commit()
    await db.refresh(grid)
    return grid


@router.delete("/{grid_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_grid(
    grid_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    await require_grid_permission(db, user, grid_id, "manage")
    grid = await get_or_404(db, Grid, grid_id, "Grid not found")
    await db.delete(grid)
    await db.commit()
