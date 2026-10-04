"""Endpoints scoped to a specific daily_log: feedings, harvests, water, treatments."""
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import DailyLog, FeedingSession, Grid, Harvest, Treatment, Warehouse, WaterParameters
from app.schemas import (
    FeedingCreate,
    FeedingOut,
    FeedingUpdate,
    HarvestCreate,
    HarvestOut,
    HarvestUpdate,
    TreatmentCreate,
    TreatmentItemIn,
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
from app.services.additives import resolve_additives
from app.services.harvest_sample import resync_harvest_sample
from app.services.common import apply_updates, get_or_404
from app.services.inventory import MovementError, consume_products, reverse_source
from app.services.products import ProductError, expand, load_catalog

router = APIRouter(tags=["days"])


def _estimated_harvest_count(biomass_kg: Decimal, sampled_abw_g: Decimal) -> int:
    return int(((biomass_kg * Decimal("1000")) / sampled_abw_g).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


_ATTRIBUTION_FIELDS = {"updated_by", "updated_by_type"}


def _feeding_payload(payload: FeedingCreate | FeedingUpdate, exclude_unset: bool = False) -> dict:
    data = payload.model_dump(exclude_unset=exclude_unset, exclude=_ATTRIBUTION_FIELDS)
    json_data = payload.model_dump(mode="json", exclude_unset=exclude_unset, exclude=_ATTRIBUTION_FIELDS)
    if "feed_types" in data:
        data["feed_types"] = json_data["feed_types"]
    return data


def _resolve_updated_by(payload: FeedingCreate | FeedingUpdate, user: CurrentUser) -> tuple[str, str]:
    updated_by_type = payload.updated_by_type or "human"
    updated_by = payload.updated_by or user.email or user.id
    return updated_by, updated_by_type


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
    access = await require_daily_log_permission(db, user, daily_log_id, "add")
    log = await get_or_404(db, DailyLog, daily_log_id, "Daily log not found")
    updated_by, updated_by_type = _resolve_updated_by(payload, user)
    data = _feeding_payload(payload)
    data["additives"] = await resolve_additives(
        db,
        farm_id=access.farm_id,
        cycle_id=log.cycle_id,
        entries=payload.additives,
        log_date=log.date,
        feed_time=payload.feed_time,
    )
    feeding = FeedingSession(
        daily_log_id=daily_log_id,
        updated_by=updated_by,
        updated_by_type=updated_by_type,
        **data,
    )
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
    access = await require_feeding_permission(db, user, feeding_id, "manage")
    feeding = await get_or_404(db, FeedingSession, feeding_id, "Feeding not found")

    updated_by, updated_by_type = _resolve_updated_by(payload, user)
    if updated_by_type == "ai" and feeding.updated_by_type == "human":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This feeding was last updated by a human and cannot be overwritten by an AI update.",
        )

    data = _feeding_payload(payload, exclude_unset=True)
    if payload.additives is not None:
        log = await get_or_404(db, DailyLog, feeding.daily_log_id, "Daily log not found")
        data["additives"] = await resolve_additives(
            db,
            farm_id=access.farm_id,
            cycle_id=log.cycle_id,
            entries=payload.additives,
            log_date=log.date,
            feed_time=payload.feed_time or feeding.feed_time,
            feeding_id=feeding.id,
        )
    for k, v in data.items():
        setattr(feeding, k, v)
    feeding.updated_by = updated_by
    feeding.updated_by_type = updated_by_type
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


async def _ensure_last_harvest_day(db: AsyncSession, log: DailyLog) -> None:
    """Harvests only change on the cycle's last harvest day (or a later day). Once a later
    harvest exists an earlier harvest day is closed, so its numbers - and the ABW sample
    taken from them - stay as they were."""
    later = (
        await db.execute(
            select(func.max(DailyLog.date))
            .join(Harvest, Harvest.daily_log_id == DailyLog.id)
            .where(DailyLog.cycle_id == log.cycle_id, DailyLog.date > log.date)
        )
    ).scalar()
    if later is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Harvests before the last harvest day ({later.isoformat()}) can't be changed",
        )


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
    log = await get_or_404(db, DailyLog, daily_log_id, "Daily log not found")
    await _ensure_last_harvest_day(db, log)
    data = payload.model_dump()
    data["estimated_count"] = _estimated_harvest_count(payload.biomass_kg, payload.sampled_abw_g)
    harvest = Harvest(daily_log_id=daily_log_id, **data)
    db.add(harvest)
    await db.flush()
    await resync_harvest_sample(db, daily_log_id)
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
    await _ensure_last_harvest_day(db, await get_or_404(db, DailyLog, harvest.daily_log_id, "Daily log not found"))
    apply_updates(harvest, payload)
    if payload.biomass_kg is not None or payload.sampled_abw_g is not None:
        harvest.estimated_count = _estimated_harvest_count(harvest.biomass_kg, harvest.sampled_abw_g)
    await db.flush()
    await resync_harvest_sample(db, harvest.daily_log_id)
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
    daily_log_id = harvest.daily_log_id
    await _ensure_last_harvest_day(db, await get_or_404(db, DailyLog, daily_log_id, "Daily log not found"))
    await db.delete(harvest)
    await db.flush()
    await resync_harvest_sample(db, daily_log_id)
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


def _amount_text(value: Decimal) -> str:
    # Stored as text so JSON keeps the exact decimal, as feeding additives do.
    return format(value.normalize(), "f")


def _summarise(items: list[dict]) -> str:
    """The one-line `action` a products-only treatment gets, so old readers still work."""
    return ", ".join(f"{item['name']} {item['amount']} {item['unit']}" for item in items)


async def _resolve_treatment_items(
    db: AsyncSession,
    farm_id: UUID,
    warehouse_id: UUID | None,
    items: list[TreatmentItemIn],
) -> tuple[list[dict], dict[UUID, Decimal]]:
    """Stored item lines, plus what they expand to in stock: {product: base amount}."""
    if not items:
        return [], {}
    if warehouse_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Say which warehouse the products came from")
    # A warehouse on another farm would otherwise be a way to spend someone else's stock.
    warehouse = await get_or_404(db, Warehouse, warehouse_id, "Warehouse not found")
    grid = await get_or_404(db, Grid, warehouse.grid_id, "Grid not found")
    if grid.farm_id != farm_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That warehouse belongs to another farm")

    catalog = await load_catalog(db, farm_id)
    stored: list[dict] = []
    lines: list[tuple[UUID, Decimal]] = []
    seen: set[UUID] = set()
    try:
        for item in items:
            product = catalog.get(item.product_id)
            if product.id in seen:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"{product.name} is listed twice on this treatment"
                )
            seen.add(product.id)
            base = catalog.to_base(product, item.amount, item.unit)
            stored.append(
                {
                    "product_id": str(product.id),
                    "name": product.name,
                    "amount": _amount_text(item.amount),
                    "unit": (item.unit or product.base_unit).strip(),
                    "base_amount": _amount_text(base),
                    "base_unit": product.base_unit,
                }
            )
            lines.append((product.id, base))
        totals = expand(catalog, lines)
    except ProductError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return stored, totals


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
    """Log a treatment, and take what it used out of the warehouse in the same transaction."""
    access = await require_daily_log_permission(db, user, daily_log_id, "add")
    await get_or_404(db, DailyLog, daily_log_id, "Daily log not found")
    stored, totals = await _resolve_treatment_items(db, access.farm_id, payload.warehouse_id, payload.items)
    treatment = Treatment(
        daily_log_id=daily_log_id,
        treatment_time=payload.treatment_time,
        action=(payload.action or "").strip() or _summarise(stored),
        worker=payload.worker,
        notes=payload.notes,
        warehouse_id=payload.warehouse_id,
        items=stored,
    )
    db.add(treatment)
    # The movements point at the treatment, so it needs its id before they are written.
    await db.flush()
    await _move_stock(db, user, treatment, totals, reverse_first=False)
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
    """Editing the products puts the old stock back and takes the new amounts out."""
    access = await require_treatment_permission(db, user, treatment_id, "manage")
    treatment = await get_or_404(db, Treatment, treatment_id, "Treatment not found")
    fields = payload.model_dump(exclude_unset=True, exclude={"items", "warehouse_id"})
    for key, value in fields.items():
        setattr(treatment, key, value)

    if payload.items is not None:
        warehouse_id = payload.warehouse_id or treatment.warehouse_id
        stored, totals = await _resolve_treatment_items(db, access.farm_id, warehouse_id, payload.items)
        treatment.items = stored
        treatment.warehouse_id = warehouse_id if stored else None
        if stored and not (payload.action or "").strip():
            treatment.action = _summarise(stored)
        await _move_stock(db, user, treatment, totals, reverse_first=True)
    elif payload.warehouse_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Send the products too when changing the warehouse, so the stock moves with them",
        )

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
    # The reversing movements stay behind on purpose: the ledger is append-only,
    # so the history still shows the stock went out and came back.
    await _reverse(db, user, treatment.id)
    await db.delete(treatment)
    await db.commit()


async def _move_stock(
    db: AsyncSession,
    user: CurrentUser,
    treatment: Treatment,
    totals: dict[UUID, Decimal],
    *,
    reverse_first: bool,
) -> None:
    """Apply a treatment's stock effect. Never commits, so a failure rolls the lot back."""
    if reverse_first:
        await _reverse(db, user, treatment.id)
    if not totals:
        return
    try:
        await consume_products(
            db,
            treatment.warehouse_id,
            totals,
            source_type="treatment",
            source_id=treatment.id,
            note=f"Treatment {treatment.treatment_time:%H:%M}",
            created_by=user.email or user.id,
        )
    except MovementError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


async def _reverse(db: AsyncSession, user: CurrentUser, treatment_id: UUID) -> None:
    try:
        await reverse_source(
            db,
            "treatment",
            treatment_id,
            note="Treatment changed",
            created_by=user.email or user.id,
        )
    except MovementError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
