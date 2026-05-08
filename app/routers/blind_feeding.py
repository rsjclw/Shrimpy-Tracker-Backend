from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import BlindFeedingTemplate
from app.schemas import (
    BlindFeedingTemplateCreate,
    BlindFeedingTemplateOut,
    BlindFeedingTemplateUpdate,
)
from app.services.access import accessible_farm_ids, require_farm_permission
from app.services.common import get_or_404

router = APIRouter(prefix="/blind-feeding-templates", tags=["blind-feeding-templates"])

_CONFLICT = HTTPException(status.HTTP_409_CONFLICT, "Blind feeding template already exists")


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Template name is required")
    return cleaned


def _template_out(template: BlindFeedingTemplate) -> BlindFeedingTemplateOut:
    values = list(template.daily_feed_per_100k)
    return BlindFeedingTemplateOut(
        id=template.id,
        farm_id=template.farm_id,
        name=template.name,
        daily_feed_per_100k=values,
        created_at=template.created_at,
        duration_days=len(values),
        cumulative_feed_per_100k=sum(values),
    )


@router.get("", response_model=list[BlindFeedingTemplateOut])
async def list_blind_feeding_templates(
    farm_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[BlindFeedingTemplateOut]:
    if farm_id:
        await require_farm_permission(db, user, farm_id)
        stmt = select(BlindFeedingTemplate).where(BlindFeedingTemplate.farm_id == farm_id)
    else:
        farm_ids = await accessible_farm_ids(db, user)
        if not farm_ids:
            return []
        stmt = select(BlindFeedingTemplate).where(BlindFeedingTemplate.farm_id.in_(farm_ids))
    result = await db.execute(stmt.order_by(BlindFeedingTemplate.created_at, BlindFeedingTemplate.name))
    return [_template_out(template) for template in result.scalars().all()]


@router.post("", response_model=BlindFeedingTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_blind_feeding_template(
    payload: BlindFeedingTemplateCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> BlindFeedingTemplateOut:
    await require_farm_permission(db, user, payload.farm_id, "add")
    template = BlindFeedingTemplate(
        farm_id=payload.farm_id,
        name=_clean_name(payload.name),
        daily_feed_per_100k=payload.daily_feed_per_100k,
    )
    db.add(template)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _CONFLICT
    await db.refresh(template)
    return _template_out(template)


@router.put("/{template_id}", response_model=BlindFeedingTemplateOut)
async def update_blind_feeding_template(
    template_id: UUID,
    payload: BlindFeedingTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> BlindFeedingTemplateOut:
    template = await get_or_404(db, BlindFeedingTemplate, template_id, "Blind feeding template not found")
    await require_farm_permission(db, user, template.farm_id, "manage")
    template.name = _clean_name(payload.name)
    template.daily_feed_per_100k = payload.daily_feed_per_100k
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _CONFLICT
    await db.refresh(template)
    return _template_out(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_blind_feeding_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    template = await get_or_404(db, BlindFeedingTemplate, template_id, "Blind feeding template not found")
    await require_farm_permission(db, user, template.farm_id, "manage")
    await db.delete(template)
    await db.commit()
