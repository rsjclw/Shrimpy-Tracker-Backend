from collections.abc import Callable
from datetime import date as ddate, time as dtime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import (
    BlindFeedingTemplate,
    Cycle,
    DailyLog,
    FeedingSession,
    Grid,
    Harvest,
    Pond,
    PopulationSample,
    Treatment,
    WaterParameters,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas import (
    CycleCreate,
    CycleOut,
    DailyLogUpdate,
    DaySummary,
    DayView,
    PopulationSampleCreate,
    PopulationSampleOut,
    PredictionConfig,
    PredictionJobOut,
    PredictionRequest,
    PredictionResultOut,
    TrendPoint,
    TrendSeries,
)


class CycleUpdate(BaseModel):
    name: str | None = None
    start_date: ddate | None = None
    initial_population: int | None = Field(default=None, gt=0)
    initial_abw_g: Decimal | None = Field(default=None, ge=0)
    # null clears the template; with recalculate_blind_feeding that removes the blind feeding.
    blind_feeding_template_id: UUID | None = None
    blind_feeding_target_abw_g: Decimal | None = Field(default=None, gt=0)
    # Changing the start date or population leaves the feedings alone unless this is set;
    # then the blind-feeding rows are rewritten from scratch. Changing the template or its
    # target always rewrites them: those are the plan itself.
    recalculate_blind_feeding: bool = False
    planned_end_date: ddate | None = None
    actual_end_date: ddate | None = None
    status: str | None = None
    maximum_daily_feed_capacity_kg: Decimal | None = None
    stable_carrying_capacity_kg_per_m3: Decimal | None = None
    final_carrying_capacity_kg_per_m3: Decimal | None = None
    feeding_index_increment: Decimal | None = None
    maximum_feeding_index: Decimal | None = None
    prediction_config: PredictionConfig | None = None
    notes: str | None = None


class BatchFeedingIn(BaseModel):
    feed_time: dtime
    amount_kg: Decimal

    @field_validator("amount_kg")
    @classmethod
    def round_amount_kg(cls, value: Decimal) -> Decimal:
        return round_feed_amount_kg(value)


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


class LateFeedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: ddate
    feed_time: dtime
    amount_kg: Decimal


class FinishCheckOut(BaseModel):
    """What ending the cycle on end_date gives, and what would make it wrong (services/finish_check.py)."""

    model_config = ConfigDict(from_attributes=True)

    end_date: ddate
    stocked: int
    harvested_count: int
    harvested_kg: Decimal
    survival_rate_pct: Decimal | None
    final_population: int
    feed_kg: Decimal
    cycle_fcr: Decimal | None
    last_harvest_date: ddate | None
    last_harvest_time: dtime | None
    harvests_after_end: int
    feeds_after_final_harvest: list[LateFeedOut]
    sample_before_final_harvest: dtime | None


class PredictionBaselineOut(BaseModel):
    previous_biomass_kg: Decimal
    feed_since_previous_sample_start_kg: Decimal
    estimated_population: int
    harvested_biomass_since_previous_sample_kg: Decimal
    initial_abw_g: Decimal | None = None


from app.services.day_view import (
    MAX_DAY_VIEW_SPAN,
    _gather as gather_cycle_rows,
    get_day_view,
    get_day_views,
    get_harvest_dates,
    get_prediction_baseline,
    get_sampling_dates,
    get_trend,
    list_day_summaries,
)
from app.services.access import (
    accessible_farm_ids,
    require_cycle_permission,
    require_farm_permission,
    require_pond_permission,
)
from app.services.blind_feeding import (
    BLIND_NOTE,
    TARGET_SAMPLE_TIME,
    BlindFeedingPlan,
    blind_feeding_window,
    plan_blind_feeding,
)
from app.services.clock import farm_today
from app.services.common import get_or_404
from app.services.finish_check import FinishCheck, finish_check
from app.services.metrics import cycle_end_date
from app.services.feeding_amounts import round_feed_amount_kg
from app.services.prediction import PredictionError, apply_prediction_result, generate_prediction, preview_prediction
from app.services.prediction_jobs import (
    get_latest_active_prediction_job,
    get_prediction_job,
    mark_prediction_job_applied,
    start_prediction_job,
)

router = APIRouter(prefix="/cycles", tags=["cycles"])


def _cycle_payload(payload: CycleCreate | CycleUpdate, exclude_unset: bool = False) -> dict:
    data = payload.model_dump(exclude_unset=exclude_unset)
    json_data = payload.model_dump(mode="json", exclude_unset=exclude_unset)
    if "prediction_config" in data:
        data["prediction_config"] = json_data["prediction_config"]
    return data


def _closed_cycle_end_date(cycle: Cycle, today: ddate) -> ddate | None:
    """Return the last plottable date for a closed cycle."""
    return cycle_end_date(cycle, today)


async def _pond_farm_id_and_feed_time(db: AsyncSession, pond_id: UUID) -> tuple[UUID, dtime]:
    result = await db.execute(
        select(Grid.farm_id, Pond.default_feed_time)
        .join(Pond, Pond.grid_id == Grid.id)
        .where(Pond.id == pond_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pond not found")
    return row


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


def _blind_feeding_plan(cycle: Cycle, template: BlindFeedingTemplate, anchor_feed_time: dtime) -> BlindFeedingPlan:
    return plan_blind_feeding(
        cycle.start_date,
        cycle.initial_population,
        template.daily_feed_per_100k,
        anchor_feed_time,
        cycle.blind_feeding_target_abw_g,
    )


def _write_blind_feedings(
    plan: BlindFeedingPlan, template: BlindFeedingTemplate, log_for: Callable[[ddate], DailyLog]
) -> None:
    for day in plan.days:
        log = log_for(day.date)
        for feed_time, amount_kg in day.sessions:
            log.feedings.append(
                FeedingSession(
                    feed_time=feed_time,
                    amount_kg=amount_kg,
                    additives=[],
                    feed_types=[],
                    notes=f"{BLIND_NOTE}{template.name} DOC {day.doc}",
                )
            )
    if plan.target_sample is not None:
        sample_date, abw_g = plan.target_sample
        log = log_for(sample_date)
        log.abw_g = abw_g
        log.abw_sample_time = TARGET_SAMPLE_TIME


async def _template_by_id(db: AsyncSession, template_id: UUID | None) -> BlindFeedingTemplate | None:
    return await db.get(BlindFeedingTemplate, template_id) if template_id else None


async def _rewrite_blind_feeding(
    db: AsyncSession,
    cycle: Cycle,
    old_start: ddate,
    old_template_id: UUID | None,
    old_target_abw_g: Decimal | None,
    anchor_feed_time: dtime,
) -> None:
    """Strictly redo the blind feeding after its inputs changed: clear every feeding
    the template wrote (wherever an earlier "just move" left it) and every feeding
    inside the old and the new template window, drop the old target-ABW sample, then
    write the plan from the cycle's current start date, population, template and
    target. Nothing in there is kept, edited or not - the user chose this."""
    old_template = await _template_by_id(db, old_template_id)
    new_template = await _template_by_id(db, cycle.blind_feeding_template_id)
    old_days = len(old_template.daily_feed_per_100k) if old_template else 0
    new_days = len(new_template.daily_feed_per_100k) if new_template else 0
    windows = [
        w for w in (blind_feeding_window(old_start, old_days), blind_feeding_window(cycle.start_date, new_days)) if w
    ]
    cycle_logs = select(DailyLog.id).where(DailyLog.cycle_id == cycle.id)
    template_rows = and_(FeedingSession.daily_log_id.in_(cycle_logs), FeedingSession.notes.like(f"{BLIND_NOTE}%"))
    first_row_day, last_row_day = (
        await db.execute(
            select(func.min(DailyLog.date), func.max(DailyLog.date))
            .join(FeedingSession, FeedingSession.daily_log_id == DailyLog.id)
            .where(template_rows)
        )
    ).one()

    # Where the template put its target sample: the day after its window, as planned or as the rows now sit.
    sample_dates = set()
    if old_target_abw_g is not None:
        if old_template:
            sample_dates.add(old_start + timedelta(days=old_days))
        if last_row_day is not None:
            sample_dates.add(last_row_day + timedelta(days=1))
    touched = [DailyLog.date.between(first, last) for first, last in windows]
    if first_row_day is not None:
        touched.append(DailyLog.date.between(first_row_day, last_row_day))
    if sample_dates:
        touched.append(DailyLog.date.in_(sorted(sample_dates)))
    if not touched:
        return

    cleared = template_rows
    if windows:
        window_logs = cycle_logs.where(or_(*(DailyLog.date.between(first, last) for first, last in windows)))
        cleared = or_(template_rows, FeedingSession.daily_log_id.in_(window_logs))
    await db.execute(delete(FeedingSession).where(cleared))
    if sample_dates:
        # The sample the template wrote: one of those dates, its dawn time and the old target value.
        await db.execute(
            update(DailyLog)
            .where(
                DailyLog.cycle_id == cycle.id,
                DailyLog.date.in_(sorted(sample_dates)),
                DailyLog.abw_g == old_target_abw_g,
                DailyLog.abw_sample_time == TARGET_SAMPLE_TIME,
            )
            .values(abw_g=None, abw_sample_time=None)
        )

    if new_template is not None:
        plan = _blind_feeding_plan(cycle, new_template, anchor_feed_time)
        plan_dates = [day.date for day in plan.days]
        if plan.target_sample is not None:
            plan_dates.append(plan.target_sample[0])
        result = await db.execute(
            select(DailyLog)
            .options(selectinload(DailyLog.feedings))
            .where(DailyLog.cycle_id == cycle.id, DailyLog.date.in_(plan_dates))
            .execution_options(populate_existing=True)
        )
        logs = {log.date: log for log in result.scalars()}

        def log_for(day: ddate) -> DailyLog:
            log = logs.get(day)
            if log is None:
                log = DailyLog(cycle_id=cycle.id, date=day, feedings=[])
                db.add(log)
                logs[day] = log
            return log

        _write_blind_feedings(plan, new_template, log_for)
        await db.flush()

    # Days the clearing left with nothing on them go too, so a moved plan leaves no empty days behind.
    await db.execute(
        delete(DailyLog).where(
            DailyLog.cycle_id == cycle.id,
            or_(*touched),
            DailyLog.abw_g.is_(None),
            DailyLog.notes.is_(None),
            ~exists().where(FeedingSession.daily_log_id == DailyLog.id),
            ~exists().where(WaterParameters.daily_log_id == DailyLog.id),
            ~exists().where(Treatment.daily_log_id == DailyLog.id),
            ~exists().where(Harvest.daily_log_id == DailyLog.id),
        )
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
    farm_id, default_feed_time = await _pond_farm_id_and_feed_time(db, payload.pond_id)
    template = await _get_cycle_template(db, payload.blind_feeding_template_id, farm_id)
    data = _cycle_payload(payload)
    cycle = Cycle(**data)
    if template:
        plan = _blind_feeding_plan(cycle, template, default_feed_time)
        _write_blind_feedings(plan, template, lambda day: DailyLog(cycle=cycle, date=day))
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
    data = _cycle_payload(payload, exclude_unset=True)
    recalculate = data.pop("recalculate_blind_feeding", False)
    # A new template or target always rewrites the plan, so the cycle never names a plan
    # its feedings and target sample don't follow (a recalculation finds the old sample by
    # the cycle's target, which must still be the value that was written).
    if any(key in data and data[key] != getattr(cycle, key) for key in ("blind_feeding_template_id", "blind_feeding_target_abw_g")):
        recalculate = True
    for key in ("start_date", "initial_population", "initial_abw_g"):
        if key in data and data[key] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{key} cannot be empty")
    next_status = data.get("status", cycle.status)

    # Closing a cycle must establish a durable end date. For a planned end in
    # the past, use that as the fallback; otherwise the close happened today.
    # Reopening clears an automatically maintained end unless the caller
    # explicitly supplied one.
    if "actual_end_date" not in data:
        if next_status != "active" and cycle.actual_end_date is None:
            today = farm_today()
            planned_end = data.get("planned_end_date", cycle.planned_end_date)
            data["actual_end_date"] = min(planned_end, today) if planned_end else today
        elif next_status == "active" and cycle.status != "active":
            data["actual_end_date"] = None

    # Only judge the dates this request sets, so an unrelated edit never trips over old data.
    sent = payload.model_fields_set
    start_date = data.get("start_date", cycle.start_date)
    end_date = data.get("actual_end_date", cycle.actual_end_date)
    if "start_date" in sent and next_status == "active" and start_date > farm_today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Start date cannot be in the future")
    if sent & {"start_date", "actual_end_date"} and end_date is not None and start_date > end_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Start date cannot be after the cycle's end date")

    # A cycle cannot end before its last harvest: that harvest would fall outside it, and
    # the survival rate and FCR of its last day would miss it. Only harvests up to today
    # count: a later-dated one is a prediction, which ending the cycle simply leaves behind.
    if end_date is not None and (next_status != "active" or "actual_end_date" in sent):
        last_harvest = (
            await db.execute(
                select(func.max(DailyLog.date))
                .join(Harvest, Harvest.daily_log_id == DailyLog.id)
                .where(DailyLog.cycle_id == cycle.id, DailyLog.date <= farm_today())
            )
        ).scalar()
        if last_harvest is not None and end_date < last_harvest:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"The last harvest is on {last_harvest.isoformat()}; the cycle can't end before it",
            )

    anchor_feed_time = None
    if data.get("blind_feeding_template_id") is not None or recalculate:
        farm_id, anchor_feed_time = await _pond_farm_id_and_feed_time(db, cycle.pond_id)
        await _get_cycle_template(db, data.get("blind_feeding_template_id"), farm_id)

    old_start, old_template_id, old_target = cycle.start_date, cycle.blind_feeding_template_id, cycle.blind_feeding_target_abw_g
    for key, value in data.items():
        setattr(cycle, key, value)
    if recalculate:
        await _rewrite_blind_feeding(db, cycle, old_start, old_template_id, old_target, anchor_feed_time)
    await db.commit()
    await db.refresh(cycle)
    return cycle


@router.get("/{cycle_id}/finish-check", response_model=FinishCheckOut)
async def get_finish_check(
    cycle_id: UUID,
    end_date: ddate = Query(...),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FinishCheck:
    """Preview of finishing on end_date: survival rate, cycle FCR, and the order mistakes to fix first."""
    await require_cycle_permission(db, user, cycle_id)
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    feedings, _samples, abw_history, harvests = await gather_cycle_rows(db, cycle)
    return finish_check(end_date, cycle.initial_population, feedings, harvests, abw_history, farm_today())


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


@router.get("/{cycle_id}/day-views", response_model=list[DayView])
async def list_cycle_day_views(
    cycle_id: UUID,
    date_from: ddate = Query(..., alias="from"),
    date_to: ddate = Query(..., alias="to"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[DayView]:
    """Full day views for a span, oldest first - one request instead of one per day."""
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="'to' must not be before 'from'")
    if (date_to - date_from).days + 1 > MAX_DAY_VIEW_SPAN:
        raise HTTPException(status_code=422, detail=f"At most {MAX_DAY_VIEW_SPAN} days per request")
    await require_cycle_permission(db, user, cycle_id)
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    return await get_day_views(db, cycle, date_from, date_to)


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
    today = farm_today()
    cycle_end = _closed_cycle_end_date(cycle, today)
    effective_to = min(date_to, cycle_end) if cycle_end else date_to
    try:
        raw = await get_trend(db, cycle, metric, date_from, effective_to)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    sampling_dates = await get_sampling_dates(db, cycle, date_from, effective_to)
    harvest_dates = await get_harvest_dates(db, cycle, date_from, effective_to)
    points = [
        TrendPoint(
            date=d,
            value=v,
            is_future=d > today,
            is_sampling_day=d in sampling_dates,
            is_harvest_day=d in harvest_dates,
        )
        for d, v in raw
    ]
    return TrendSeries(metric=metric, points=points)


@router.get("/{cycle_id}/prediction-baseline", response_model=PredictionBaselineOut)
async def get_cycle_prediction_baseline(
    cycle_id: UUID,
    start_date: ddate = Query(...),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PredictionBaselineOut:
    await require_cycle_permission(db, user, cycle_id, "manage")
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    baseline = await get_prediction_baseline(db, cycle, start_date)
    return PredictionBaselineOut(**baseline)


@router.post("/{cycle_id}/prediction/preview", response_model=PredictionResultOut)
async def preview_cycle_prediction(
    cycle_id: UUID,
    payload: PredictionRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PredictionResultOut:
    await require_cycle_permission(db, user, cycle_id, "manage")
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    try:
        return await preview_prediction(
            db,
            cycle,
            payload.start_date,
            payload.target_doc,
            payload.optimize_partial_harvests,
        )
    except PredictionError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error))


@router.post("/{cycle_id}/prediction/preview-jobs", response_model=PredictionJobOut)
async def start_cycle_prediction_preview_job(
    cycle_id: UUID,
    payload: PredictionRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PredictionJobOut:
    await require_cycle_permission(db, user, cycle_id, "manage")
    await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    return start_prediction_job(cycle_id, user.id, payload)


@router.get("/{cycle_id}/prediction/preview-jobs/latest", response_model=PredictionJobOut | None)
async def get_latest_cycle_prediction_preview_job(
    cycle_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PredictionJobOut | None:
    await require_cycle_permission(db, user, cycle_id)
    return get_latest_active_prediction_job(cycle_id, user.id)


@router.get("/{cycle_id}/prediction/preview-jobs/{job_id}", response_model=PredictionJobOut)
async def get_cycle_prediction_preview_job(
    cycle_id: UUID,
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PredictionJobOut:
    await require_cycle_permission(db, user, cycle_id)
    job = get_prediction_job(job_id, cycle_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prediction job not found")
    return job


@router.post("/{cycle_id}/prediction/preview-jobs/{job_id}/generate", response_model=PredictionResultOut)
async def generate_cycle_prediction_from_preview_job(
    cycle_id: UUID,
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PredictionResultOut:
    await require_cycle_permission(db, user, cycle_id, "manage")
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    job = get_prediction_job(job_id, cycle_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prediction job not found")
    if job.status != "completed" or job.result is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Prediction job is not complete")
    result = await apply_prediction_result(db, cycle, job.result)
    mark_prediction_job_applied(job)
    return result


@router.post("/{cycle_id}/prediction/generate", response_model=PredictionResultOut)
async def generate_cycle_prediction(
    cycle_id: UUID,
    payload: PredictionRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PredictionResultOut:
    await require_cycle_permission(db, user, cycle_id, "manage")
    cycle = await get_or_404(db, Cycle, cycle_id, "Cycle not found")
    try:
        return await generate_prediction(
            db,
            cycle,
            payload.start_date,
            payload.target_doc,
            payload.optimize_partial_harvests,
        )
    except PredictionError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error))


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
    first_target_date = min(target_dates)
    feedings_deleted = 0

    if payload.replace_feedings:
        existing_result = await db.execute(
            select(DailyLog.id).where(
                DailyLog.cycle_id == cycle_id,
                DailyLog.date >= first_target_date,
            )
        )
        existing_log_ids = list(existing_result.scalars().all())
        if existing_log_ids:
            delete_result = await db.execute(
                delete(FeedingSession).where(FeedingSession.daily_log_id.in_(existing_log_ids))
            )
            feedings_deleted = delete_result.rowcount or 0
            await db.execute(delete(WaterParameters).where(WaterParameters.daily_log_id.in_(existing_log_ids)))
            await db.execute(delete(Harvest).where(Harvest.daily_log_id.in_(existing_log_ids)))
            await db.execute(delete(Treatment).where(Treatment.daily_log_id.in_(existing_log_ids)))
            await db.execute(delete(DailyLog).where(DailyLog.id.in_(existing_log_ids)))
        await db.execute(
            delete(PopulationSample).where(
                PopulationSample.cycle_id == cycle_id,
                PopulationSample.date >= first_target_date,
            )
        )

    result = await db.execute(
        select(DailyLog)
        .where(DailyLog.cycle_id == cycle_id, DailyLog.date.in_(target_dates))
    )
    logs_by_date = {log.date: log for log in result.scalars().all()}

    feedings_created = 0
    feedings_updated = 0
    abw_samples_written = 0

    for day in payload.days:
        if day.date not in logs_by_date:
            log = DailyLog(cycle_id=cycle_id, date=day.date)
            db.add(log)
            logs_by_date[day.date] = log

    await db.flush()

    log_ids = [log.id for log in logs_by_date.values()]
    existing_by_log_id: dict[UUID, dict[dtime, FeedingSession]] = {
        log.id: {} for log in logs_by_date.values()
    }
    if payload.replace_feedings:
        if log_ids:
            delete_result = await db.execute(
                delete(FeedingSession).where(FeedingSession.daily_log_id.in_(log_ids))
            )
            feedings_deleted += delete_result.rowcount or 0
    elif log_ids:
        feedings_result = await db.execute(
            select(FeedingSession).where(FeedingSession.daily_log_id.in_(log_ids))
        )
        for feeding in feedings_result.scalars().all():
            existing_by_log_id.setdefault(feeding.daily_log_id, {})[feeding.feed_time] = feeding

    for day in payload.days:
        log = logs_by_date[day.date]

        if day.abw_g is not None:
            log.abw_g = day.abw_g
            log.abw_sample_time = payload.abw_sample_time
            abw_samples_written += 1

        existing_by_time = existing_by_log_id.setdefault(log.id, {})

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
