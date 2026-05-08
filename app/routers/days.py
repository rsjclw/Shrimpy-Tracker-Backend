"""Endpoints scoped to a specific daily_log: feedings, harvests, water, treatments."""
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import DailyLog, FeedingSession, Harvest, Treatment, WaterParameters
from app.schemas import (
    FeedingCreate,
    FeedingOut,
    FeedingUpdate,
    HarvestCreate,
    HarvestOut,
    HarvestUpdate,
    TreatmentCreate,
    TreatmentOut,
    TreatmentUpdate,
    WaterParametersOut,
    WaterParametersUpsert,
)
from app.services.access import (
    require_daily_log_permission,
    require_feeding_permission,
    require_harvest_permission,
    require_treatment_permission,
)
from app.services.common import apply_updates, get_or_404

router = APIRouter(tags=["days"])


def _estimated_harvest_count(biomass_kg: Decimal, sampled_abw_g: Decimal) -> int:
    return int(((biomass_kg * Decimal("1000")) / sampled_abw_g).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _feeding_payload(payload: FeedingCreate | FeedingUpdate, exclude_unset: bool = False) -> dict:
    data = payload.model_dump(exclude_unset=exclude_unset)
    json_data = payload.model_dump(mode="json", exclude_unset=exclude_unset)
    if "feed_types" in data:
        data["feed_types"] = json_data["feed_types"]
    return data


@router.post(
    "/days/{daily_log_id}/feedings",
    response_model=FeedingOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_feeding(
    daily_log_id: UUID,
    payload: FeedingCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FeedingSession:
    await require_daily_log_permission(db, user, daily_log_id, "add")
    await get_or_404(db, DailyLog, daily_log_id, "Daily log not found")
    feeding = FeedingSession(daily_log_id=daily_log_id, **_feeding_payload(payload))
    db.add(feeding)
    await db.commit()
    await db.refresh(feeding)
    return feeding


@router.put("/feedings/{feeding_id}", response_model=FeedingOut)
async def update_feeding(
    feeding_id: UUID,
    payload: FeedingUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FeedingSession:
    await require_feeding_permission(db, user, feeding_id, "manage")
    feeding = await get_or_404(db, FeedingSession, feeding_id, "Feeding not found")
    for k, v in _feeding_payload(payload, exclude_unset=True).items():
        setattr(feeding, k, v)
    await db.commit()
    await db.refresh(feeding)
    return feeding


@router.delete("/feedings/{feeding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feeding(
    feeding_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    await require_feeding_permission(db, user, feeding_id, "manage")
    feeding = await get_or_404(db, FeedingSession, feeding_id, "Feeding not found")
    await db.delete(feeding)
    await db.commit()


@router.post(
    "/days/{daily_log_id}/harvests",
    response_model=HarvestOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_harvest(
    daily_log_id: UUID,
    payload: HarvestCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Harvest:
    await require_daily_log_permission(db, user, daily_log_id, "add")
    await get_or_404(db, DailyLog, daily_log_id, "Daily log not found")
    data = payload.model_dump()
    data["estimated_count"] = _estimated_harvest_count(payload.biomass_kg, payload.sampled_abw_g)
    harvest = Harvest(daily_log_id=daily_log_id, **data)
    db.add(harvest)
    await db.commit()
    await db.refresh(harvest)
    return harvest


@router.put("/harvests/{harvest_id}", response_model=HarvestOut)
async def update_harvest(
    harvest_id: UUID,
    payload: HarvestUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Harvest:
    await require_harvest_permission(db, user, harvest_id, "manage")
    harvest = await get_or_404(db, Harvest, harvest_id, "Harvest not found")
    apply_updates(harvest, payload)
    if payload.biomass_kg is not None or payload.sampled_abw_g is not None:
        harvest.estimated_count = _estimated_harvest_count(harvest.biomass_kg, harvest.sampled_abw_g)
    await db.commit()
    await db.refresh(harvest)
    return harvest


@router.delete("/harvests/{harvest_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_harvest(
    harvest_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    await require_harvest_permission(db, user, harvest_id, "manage")
    harvest = await get_or_404(db, Harvest, harvest_id, "Harvest not found")
    await db.delete(harvest)
    await db.commit()


@router.put("/days/{daily_log_id}/water", response_model=WaterParametersOut)
async def upsert_water(
    daily_log_id: UUID,
    payload: WaterParametersUpsert,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> WaterParameters:
    await require_daily_log_permission(db, user, daily_log_id, "manage")
    await get_or_404(db, DailyLog, daily_log_id, "Daily log not found")
    result = await db.execute(
        select(WaterParameters).where(WaterParameters.daily_log_id == daily_log_id)
    )
    water = result.scalar_one_or_none()
    if not water:
        water = WaterParameters(daily_log_id=daily_log_id)
        db.add(water)
    apply_updates(water, payload)
    await db.commit()
    await db.refresh(water)
    return water


@router.post(
    "/days/{daily_log_id}/treatments",
    response_model=TreatmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_treatment(
    daily_log_id: UUID,
    payload: TreatmentCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Treatment:
    await require_daily_log_permission(db, user, daily_log_id, "add")
    await get_or_404(db, DailyLog, daily_log_id, "Daily log not found")
    treatment = Treatment(daily_log_id=daily_log_id, **payload.model_dump())
    db.add(treatment)
    await db.commit()
    await db.refresh(treatment)
    return treatment


@router.put("/treatments/{treatment_id}", response_model=TreatmentOut)
async def update_treatment(
    treatment_id: UUID,
    payload: TreatmentUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Treatment:
    await require_treatment_permission(db, user, treatment_id, "manage")
    treatment = await get_or_404(db, Treatment, treatment_id, "Treatment not found")
    apply_updates(treatment, payload)
    await db.commit()
    await db.refresh(treatment)
    return treatment


@router.delete("/treatments/{treatment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_treatment(
    treatment_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    await require_treatment_permission(db, user, treatment_id, "manage")
    treatment = await get_or_404(db, Treatment, treatment_id, "Treatment not found")
    await db.delete(treatment)
    await db.commit()
