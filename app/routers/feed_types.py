from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import FeedType
from app.schemas import FeedTypeCreate, FeedTypeOut, FeedTypeUpdate
from app.services.access import accessible_farm_ids, require_farm_permission
from app.services.common import apply_updates, get_or_404

router = APIRouter(prefix="/feed-types", tags=["feed-types"])

_CONFLICT = HTTPException(status.HTTP_409_CONFLICT, "Feed type already exists")


@router.get("", response_model=list[FeedTypeOut])
async def list_feed_types(
    farm_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[FeedType]:
    if farm_id:
        await require_farm_permission(db, user, farm_id)
        stmt = select(FeedType).where(FeedType.farm_id == farm_id)
    else:
        farm_ids = await accessible_farm_ids(db, user)
        if not farm_ids:
            return []
        stmt = select(FeedType).where(FeedType.farm_id.in_(farm_ids))
    result = await db.execute(stmt.order_by(FeedType.created_at, FeedType.brand))
    return list(result.scalars().all())


@router.post("", response_model=FeedTypeOut, status_code=status.HTTP_201_CREATED)
async def create_feed_type(
    payload: FeedTypeCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FeedType:
    await require_farm_permission(db, user, payload.farm_id, "add")
    feed_type = FeedType(**payload.model_dump())
    db.add(feed_type)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _CONFLICT
    await db.refresh(feed_type)
    return feed_type


@router.put("/{feed_type_id}", response_model=FeedTypeOut)
async def update_feed_type(
    feed_type_id: UUID,
    payload: FeedTypeUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FeedType:
    feed_type = await get_or_404(db, FeedType, feed_type_id, "Feed type not found")
    await require_farm_permission(db, user, feed_type.farm_id, "manage")
    apply_updates(feed_type, payload)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _CONFLICT
    await db.refresh(feed_type)
    return feed_type


@router.delete("/{feed_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feed_type(
    feed_type_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    feed_type = await get_or_404(db, FeedType, feed_type_id, "Feed type not found")
    await require_farm_permission(db, user, feed_type.farm_id, "manage")
    await db.delete(feed_type)
    await db.commit()
