from fastapi import APIRouter, Depends, HTTPException, status
from uuid import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import FeedAdditive
from app.schemas import AdditiveCreate, AdditiveOut, AdditiveUpdate
from app.services.access import accessible_farm_ids, require_farm_permission
from app.services.common import apply_updates, get_or_404

router = APIRouter(prefix="/additives", tags=["additives"])

_CONFLICT = HTTPException(status.HTTP_409_CONFLICT, "Additive name already exists")


@router.get("", response_model=list[AdditiveOut])
async def list_additives(
    farm_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[FeedAdditive]:
    if farm_id:
        await require_farm_permission(db, user, farm_id)
        stmt = select(FeedAdditive).where(FeedAdditive.farm_id == farm_id)
    else:
        farm_ids = await accessible_farm_ids(db, user)
        if not farm_ids:
            return []
        stmt = select(FeedAdditive).where(FeedAdditive.farm_id.in_(farm_ids))
    result = await db.execute(stmt.order_by(FeedAdditive.name))
    return list(result.scalars().all())


@router.post("", response_model=AdditiveOut, status_code=status.HTTP_201_CREATED)
async def create_additive(
    payload: AdditiveCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FeedAdditive:
    await require_farm_permission(db, user, payload.farm_id, "add")
    additive = FeedAdditive(**payload.model_dump())
    db.add(additive)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _CONFLICT
    await db.refresh(additive)
    return additive


@router.put("/{additive_id}", response_model=AdditiveOut)
async def update_additive(
    additive_id: int,
    payload: AdditiveUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FeedAdditive:
    additive = await get_or_404(db, FeedAdditive, additive_id, "Additive not found")
    await require_farm_permission(db, user, additive.farm_id, "manage")
    apply_updates(additive, payload)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _CONFLICT
    await db.refresh(additive)
    return additive


@router.delete("/{additive_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_additive(
    additive_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    additive = await get_or_404(db, FeedAdditive, additive_id, "Additive not found")
    await require_farm_permission(db, user, additive.farm_id, "manage")
    await db.delete(additive)
    await db.commit()
