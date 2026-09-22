"""Additive doses and usage per cycle, for dashboards and programmatic tracking."""
from datetime import date as ddate, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.schemas import AdditiveDoseOut, AdditiveUsageOut
from app.services.access import require_cycle_permission
from app.services.additives import current_doses, usage_by_day

router = APIRouter(prefix="/cycles", tags=["additives"])


@router.get("/{cycle_id}/additive-doses", response_model=list[AdditiveDoseOut])
async def get_additive_doses(
    cycle_id: UUID,
    as_of: ddate | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """The dose each farm additive is on for this cycle as of a date (default today).

    This is the dose a new feeding on that date gets when it leaves
    `dosage_gr_per_kg` out: the last dose used in the cycle, else the farm default.
    """
    access = await require_cycle_permission(db, user, cycle_id)
    return await current_doses(db, access.farm_id, cycle_id, as_of or datetime.now(timezone.utc).date())


@router.get("/{cycle_id}/additive-usage", response_model=list[AdditiveUsageOut])
async def get_additive_usage(
    cycle_id: UUID,
    date_from: ddate = Query(..., alias="from"),
    date_to: ddate = Query(..., alias="to"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """Per day and additive: feed carrying it, grams given, and the day's average dose."""
    if date_to < date_from:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'to' is before 'from'")
    access = await require_cycle_permission(db, user, cycle_id)
    return await usage_by_day(db, access.farm_id, cycle_id, date_from, date_to)
