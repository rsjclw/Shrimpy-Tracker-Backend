from datetime import date as ddate

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import DailyEnvironment, Grid, Pond
from app.schemas import (
    EnvironmentRefreshOut,
    GridCreate,
    GridEnvironmentOut,
    GridOut,
    GridUpdate,
    PondOut,
)
from app.services import weather
from app.services.access import accessible_farm_ids, require_farm_permission, require_grid_permission
from app.services.common import get_or_404

router = APIRouter(prefix="/grids", tags=["grids"])

COORDINATE_FIELDS = ("latitude", "longitude")


def _apply_grid_updates(grid: Grid, payload: GridUpdate) -> bool:
    """Apply an update, returning True when the coordinates changed.

    Coordinates are preserved when omitted. The rest of the payload keeps the
    existing replace-everything behaviour, but a plain rename must never
    silently drop a grid's location and with it all of its weather.
    """
    before = (grid.latitude, grid.longitude)
    data = payload.model_dump(exclude_unset=False)
    for field in COORDINATE_FIELDS:
        if field not in payload.model_fields_set:
            data.pop(field, None)
    for key, value in data.items():
        setattr(grid, key, value)
    return (grid.latitude, grid.longitude) != before


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
    await db.flush()
    if grid.latitude is not None and grid.longitude is not None:
        # Resolves timezone and elevation now, so the lunar windows and the
        # sync schedule do not sit on the default zone until 05:00.
        await weather.resolve_location(db, grid)
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


@router.get("/{grid_id}/environment", response_model=GridEnvironmentOut)
async def list_grid_environment(
    grid_id: str,
    date_from: ddate = Query(alias="from"),
    date_to: ddate = Query(alias="to"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> GridEnvironmentOut:
    await require_grid_permission(db, user, grid_id)
    grid = await get_or_404(db, Grid, grid_id, "Grid not found")
    result = await db.execute(
        select(DailyEnvironment)
        .where(
            DailyEnvironment.grid_id == grid.id,
            DailyEnvironment.date >= date_from,
            DailyEnvironment.date <= date_to,
        )
        .order_by(DailyEnvironment.date)
    )
    return GridEnvironmentOut(
        grid_id=grid.id,
        timezone=grid.timezone,
        days=list(result.scalars().all()),
    )


@router.post("/{grid_id}/environment/refresh", response_model=EnvironmentRefreshOut)
async def refresh_grid_environment(
    grid_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EnvironmentRefreshOut:
    await require_grid_permission(db, user, grid_id, "manage")
    grid = await get_or_404(db, Grid, grid_id, "Grid not found")
    if grid.latitude is None or grid.longitude is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Grid has no coordinates set"
        )
    days_written = await weather.sync_grid(db, grid)
    await db.commit()
    await db.refresh(grid)
    return EnvironmentRefreshOut(
        grid_id=grid.id,
        days_written=days_written,
        timezone=grid.timezone,
        synced_at=grid.weather_synced_at,
    )


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
    coordinates_changed = _apply_grid_updates(grid, payload)
    if coordinates_changed and grid.latitude is not None and grid.longitude is not None:
        grid.timezone = None
        grid.elevation_m = None
        await weather.resolve_location(db, grid)
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
