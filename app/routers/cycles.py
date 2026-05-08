from datetime import date as ddate, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import BlindFeedingTemplate, Cycle, DailyLog, FeedingSession, Grid, Pond, PopulationSample
from pydantic import BaseModel, Field

from app.schemas import (
    CycleCreate,
    CycleOut,
    DailyLogUpdate,
    DaySummary,
    DayView,
    PopulationSampleCreate,
    PopulationSampleOut,
    TrendPoint,
    TrendSeries,
)


class CycleUpdate(BaseModel):
    name: str | None = None
    planned_end_date: ddate | None = None
    actual_end_date: ddate | None = None
    status: str | None = None
    maximum_daily_feed_capacity_kg: Decimal | None = None
    stable_carrying_capacity_kg_per_m3: Decimal | None = None
    final_carrying_capacity_kg_per_m3: Decimal | None = None
    feeding_index_increment: Decimal | None = None
    maximum_feeding_index: Decimal | None = None
    notes: str | None = None


class BatchFeedingIn(BaseModel):
    feed_time: dtime
    amount_kg: Decimal


class BatchFeedingAbwDayIn(BaseModel):
    date: ddate
    abw_g: Decimal | None = None
    feedings: list[BatchFeedingIn] = Field(default_factory=list)


class BatchFeedingAbwImportIn(BaseModel):
    replace_feedings: bool = False
    abw_sample_time: dtime = dtime(5, 0)
    days: list[BatchFeedingAbwDayIn]


class BatchFeedingAbwImportOut(BaseModel):
    days: int
    feedings_created: int
    feedings_updated: int
    feedings_deleted: int
    abw_samples_written: int


from app.services.day_view import get_day_view, get_sampling_dates, get_trend, list_day_summaries
from app.services.access import (
    accessible_farm_ids,
    require_cycle_permission,
    require_farm_permission,
    require_pond_permission,
)
from app.services.common import apply_updates, get_or_404

router = APIRouter(prefix="/cycles", tags=["cycles"])
_BLIND_FEEDING_SESSIONS = [
    (dtime(6, 0), Decimal("0.25")),
    (dtime(10, 0), Decimal("0.30")),
    (dtime(14, 0), Decimal("0.30")),
    (dtime(18, 0), Decimal("0.15")),
]


async def _pond_farm_id(db: AsyncSession, pond_id: UUID) -> UUID:
    result = await db.execute(
        select(Grid.farm_id).join(Pond, Pond.grid_id == Grid.id).where(Pond.id == pond_id)
    )
    farm_id = result.scalar_one_or_none()
    if farm_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pond not found")
    return farm_id


async def _get_cycle_template(
    db: AsyncSession, template_id: UUID | None, farm_id: UUID
) -> BlindFeedingTemplate | None:
    if template_id is None:
        return None
    template = await get_or_404(
        db, BlindFeedingTemplate, template_id, "Blind feeding template not found"
    )
    if template.farm_id != farm_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Blind feeding template belongs to another farm")
    return template


def _blind_feeding_amount(rate_per_100k: float, population: int) -> Decimal:
    amount = Decimal(str(rate_per_100k)) * Decimal(population) / Decimal("100000")
    return amount.quantize(Decimal("0.001"))


def _add_blind_feedings(cycle: Cycle, template: BlindFeedingTemplate) -> None:
    for day_index, rate in enumerate(template.daily_feed_per_100k):
        total = _blind_feeding_amount(rate, cycle.initial_population)
        log = DailyLog(cycle=cycle, date=cycle.start_date + timedelta(days=day_index))
        for feed_time, fraction in _BLIND_FEEDING_SESSIONS:
            log.feedings.append(
                FeedingSession(
                    feed_time=feed_time,
                    amount_kg=(total * fraction).quantize(Decimal("0.001")),
                    additives=[],
                    feed_types=[],
                    notes=f"Blind feeding: {template.name} DOC {day_index + 1}",
                )
            )
    if cycle.blind_feeding_target_abw_g is not None:
        sampling_date = cycle.start_date + timedelta(days=len(template.daily_feed_per_100k))
        DailyLog(
            cycle=cycle,
            date=sampling_date,
            abw_g=cycle.blind_feeding_target_abw_g,
            abw_sample_time=dtime(5, 0),
        )


@router.get("", response_model=list[CycleOut])
async def list_cycles(
    pond_id: UUID | None = None,
    farm_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[Cycle]:
    stmt = select(Cycle).order_by(Cycle.start_date.desc())
    if pond_id:
        await require_pond_permission(db, user, pond_id)
        stmt = stmt.where(Cycle.pond_id == pond_id)
    elif farm_id:
        await require_farm_permission(db, user, farm_id)
        stmt = stmt.join(Pond, Cycle.pond_id == Pond.id).join(Grid, Pond.grid_id == Grid.id).where(Grid.farm_id == farm_id)
    else:
        farm_ids = await accessible_farm_ids(db, user)
        if not farm_ids:
            return []
        stmt = stmt.join(Pond, Cycle.pond_id == Pond.id).join(Grid, Pond.grid_id == Grid.id).where(Grid.farm_id.in_(farm_ids))
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=CycleOut, status_code=status.HTTP_201_CREATED)
async def create_cycle(
    payload: CycleCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Cycle:
    await require_pond_permission(db, user, payload.pond_id, "add")
    farm_id = await _pond_farm_id(db, payload.pond_id)
    template = await _get_cycle_template(db, payload.blind_feeding_template_id, farm_id)
    data = payload.model_dump()
    cycle = Cycle(**data)
    if template:
        _add_blind_feedings(cycle, template)
    db.add(cycle)
    await db.commit()
    await db.refresh(cycle)
    return cycle


@router.get("/{cycle_id}", response_model=CycleOut)
async def get_cycle(
    cycle_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Cycle:
    await require_cycle_permission(db, user, cycle_id)
    return await get_or_404(db, Cycle, cycle_id, "Cycle not found")


@router.put("/{cycle_id}", response_model=CycleOut)
async def update_cycle(
    cycle_id: UUID,
    payload: CycleUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Cycle:
    await require_cycle_permission(db, user, cycle_id, "manage")
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    apply_updates(cycle, payload)
    await db.commit()
    await db.refresh(cycle)
    return cycle


@router.delete("/{cycle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cycle(
    cycle_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    await require_cycle_permission(db, user, cycle_id, "manage")
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    await db.delete(cycle)
    await db.commit()


@router.get("/{cycle_id}/days", response_model=list[DaySummary])
async def list_cycle_days(
    cycle_id: UUID,
    date_from: ddate = Query(..., alias="from"),
    date_to: ddate = Query(..., alias="to"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[DaySummary]:
    await require_cycle_permission(db, user, cycle_id)
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    return await list_day_summaries(db, cycle, date_from, date_to)


@router.get("/{cycle_id}/days/{day}", response_model=DayView)
async def get_cycle_day(
    cycle_id: UUID,
    day: ddate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> DayView:
    await require_cycle_permission(db, user, cycle_id)
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    return await get_day_view(db, cycle, day)


@router.put("/{cycle_id}/days/{day}", response_model=DayView)
async def upsert_cycle_day(
    cycle_id: UUID,
    day: ddate,
    payload: DailyLogUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> DayView:
    data = payload.model_dump(exclude_unset=True)
    await require_cycle_permission(db, user, cycle_id, "add" if not data else "manage")
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    result = await db.execute(
        select(DailyLog).where(DailyLog.cycle_id == cycle_id, DailyLog.date == day)
    )
    log = result.scalar_one_or_none()
    if not log:
        log = DailyLog(cycle_id=cycle_id, date=day)
        db.add(log)
    for k, v in data.items():
        setattr(log, k, v)
    await db.commit()
    return await get_day_view(db, cycle, day)


@router.get("/{cycle_id}/trends", response_model=TrendSeries)
async def get_cycle_trend(
    cycle_id: UUID,
    metric: str = Query(...),
    date_from: ddate = Query(..., alias="from"),
    date_to: ddate = Query(..., alias="to"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> TrendSeries:
    await require_cycle_permission(db, user, cycle_id)
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    try:
        raw = await get_trend(db, cycle, metric, date_from, date_to)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    today = datetime.now(timezone.utc).date()
    sampling_dates = await get_sampling_dates(db, cycle, date_from, date_to)
    points = [
        TrendPoint(date=d, value=v, is_future=d > today, is_sampling_day=d in sampling_dates)
        for d, v in raw
    ]
    return TrendSeries(metric=metric, points=points)


@router.post("/{cycle_id}/batch-import/feedings-abw", response_model=BatchFeedingAbwImportOut)
async def batch_import_feedings_abw(
    cycle_id: UUID,
    payload: BatchFeedingAbwImportIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> BatchFeedingAbwImportOut:
    await require_cycle_permission(db, user, cycle_id, "manage")
    await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    if not payload.days:
        return BatchFeedingAbwImportOut(
            days=0,
            feedings_created=0,
            feedings_updated=0,
            feedings_deleted=0,
            abw_samples_written=0,
        )

    target_dates = {day.date for day in payload.days}
    result = await db.execute(
        select(DailyLog)
        .where(DailyLog.cycle_id == cycle_id, DailyLog.date.in_(target_dates))
        .options(selectinload(DailyLog.feedings))
    )
    logs_by_date = {log.date: log for log in result.scalars().all()}

    feedings_created = 0
    feedings_updated = 0
    feedings_deleted = 0
    abw_samples_written = 0

    for day in payload.days:
        if day.date not in logs_by_date:
            log = DailyLog(cycle_id=cycle_id, date=day.date)
            db.add(log)
            logs_by_date[day.date] = log

    await db.flush()

    for day in payload.days:
        log = logs_by_date[day.date]

        if day.abw_g is not None:
            log.abw_g = day.abw_g
            log.abw_sample_time = payload.abw_sample_time
            abw_samples_written += 1

        if payload.replace_feedings:
            for feeding in list(log.feedings):
                await db.delete(feeding)
                feedings_deleted += 1
            log.feedings.clear()
            await db.flush()
            existing_by_time = {}
        else:
            existing_by_time = {
                feeding.feed_time: feeding for feeding in log.feedings
            }

        for incoming in day.feedings:
            existing = existing_by_time.get(incoming.feed_time)
            if existing:
                existing.amount_kg = incoming.amount_kg
                feedings_updated += 1
            else:
                feeding = FeedingSession(
                    daily_log_id=log.id,
                    feed_time=incoming.feed_time,
                    amount_kg=incoming.amount_kg,
                    additives=[],
                    feed_types=[],
                )
                db.add(feeding)
                existing_by_time[incoming.feed_time] = feeding
                feedings_created += 1

    await db.commit()
    return BatchFeedingAbwImportOut(
        days=len(payload.days),
        feedings_created=feedings_created,
        feedings_updated=feedings_updated,
        feedings_deleted=feedings_deleted,
        abw_samples_written=abw_samples_written,
    )


@router.post(
    "/{cycle_id}/samples",
    response_model=PopulationSampleOut,
    status_code=status.HTTP_200_OK,
)
async def upsert_sample(
    cycle_id: UUID,
    payload: PopulationSampleCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PopulationSample:
    access = await require_cycle_permission(db, user, cycle_id, "add")
    result = await db.execute(
        select(PopulationSample).where(
            PopulationSample.cycle_id == cycle_id,
            PopulationSample.date == payload.date,
        )
    )
    sample = result.scalar_one_or_none()
    if sample:
        if access.role != "owner":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient farm permission")
        sample.population = payload.population
        if payload.method is not None:
            sample.method = payload.method
        if payload.notes is not None:
            sample.notes = payload.notes
    else:
        sample = PopulationSample(cycle_id=cycle_id, **payload.model_dump())
        db.add(sample)
    await db.commit()
    await db.refresh(sample)
    return sample
