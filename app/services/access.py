from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.config import settings
from app.models import (
    Cycle,
    DailyLog,
    Farm,
    FarmMembership,
    FeedingSession,
    Grid,
    Harvest,
    Pond,
    Treatment,
    WaterParameters,
)

ROLE_PERMISSIONS = {
    "viewer": {"read"},
    "operator": {"read", "add"},
    "owner": {"read", "add", "manage"},
    "admin": {"read", "add", "manage"},
}


@dataclass(frozen=True)
class FarmAccess:
    farm_id: UUID
    role: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def admin_emails() -> set[str]:
    return {
        normalize_email(email)
        for email in settings.admin_emails.split(",")
        if email.strip()
    }


def is_admin(user: CurrentUser) -> bool:
    return bool(user.email and normalize_email(user.email) in admin_emails())


def validate_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized not in {"owner", "operator", "viewer"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid farm role")
    return normalized


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


def require_permission(role: str, permission: str) -> None:
    if not has_permission(role, permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient farm permission")


def _user_uuid(user: CurrentUser) -> UUID | None:
    try:
        return UUID(user.id)
    except ValueError:
        return None


def _membership_filter(user: CurrentUser):
    clauses = []
    user_uuid = _user_uuid(user)
    if user_uuid:
        clauses.append(FarmMembership.user_id == user_uuid)
    if user.email:
        clauses.append(func.lower(FarmMembership.email) == normalize_email(user.email))
    if not clauses:
        return None
    return or_(*clauses)


async def _bind_user_id_if_needed(
    db: AsyncSession, membership: FarmMembership, user: CurrentUser
) -> None:
    user_uuid = _user_uuid(user)
    if user_uuid and membership.user_id is None:
        membership.user_id = user_uuid
        await db.flush()


async def list_memberships(db: AsyncSession, user: CurrentUser) -> list[FarmMembership]:
    if is_admin(user):
        return []
    membership_filter = _membership_filter(user)
    if membership_filter is None:
        return []
    result = await db.execute(select(FarmMembership).where(membership_filter))
    memberships = list(result.scalars().all())
    for membership in memberships:
        await _bind_user_id_if_needed(db, membership, user)
    return memberships


async def accessible_farm_ids(db: AsyncSession, user: CurrentUser) -> list[UUID]:
    if is_admin(user):
        result = await db.execute(select(Farm.id))
        return list(result.scalars().all())
    return [membership.farm_id for membership in await list_memberships(db, user)]


async def get_farm_access(
    db: AsyncSession, user: CurrentUser, farm_id: UUID
) -> FarmAccess | None:
    if is_admin(user):
        farm = await db.get(Farm, farm_id)
        if not farm:
            return None
        return FarmAccess(farm_id=farm_id, role="admin")
    membership_filter = _membership_filter(user)
    if membership_filter is None:
        return None
    result = await db.execute(
        select(FarmMembership).where(
            FarmMembership.farm_id == farm_id,
            membership_filter,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        return None
    await _bind_user_id_if_needed(db, membership, user)
    return FarmAccess(farm_id=membership.farm_id, role=membership.role)


async def require_farm_permission(
    db: AsyncSession,
    user: CurrentUser,
    farm_id: UUID,
    permission: str = "read",
) -> FarmAccess:
    access = await get_farm_access(db, user, farm_id)
    if not access:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Farm access denied")
    require_permission(access.role, permission)
    return access


async def require_grid_permission(
    db: AsyncSession, user: CurrentUser, grid_id: UUID | str, permission: str = "read"
) -> FarmAccess:
    grid = await db.get(Grid, grid_id)
    if not grid:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Grid not found")
    return await require_farm_permission(db, user, grid.farm_id, permission)


async def farm_id_for_pond(db: AsyncSession, pond_id: UUID | str) -> UUID:
    result = await db.execute(
        select(Grid.farm_id).join(Pond, Pond.grid_id == Grid.id).where(Pond.id == pond_id)
    )
    farm_id = result.scalar_one_or_none()
    if not farm_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pond not found")
    return farm_id


async def require_pond_permission(
    db: AsyncSession, user: CurrentUser, pond_id: UUID | str, permission: str = "read"
) -> FarmAccess:
    farm_id = await farm_id_for_pond(db, pond_id)
    return await require_farm_permission(db, user, farm_id, permission)


async def farm_id_for_cycle(db: AsyncSession, cycle_id: UUID | str) -> UUID:
    result = await db.execute(
        select(Grid.farm_id)
        .join(Pond, Pond.grid_id == Grid.id)
        .join(Cycle, Cycle.pond_id == Pond.id)
        .where(Cycle.id == cycle_id)
    )
    farm_id = result.scalar_one_or_none()
    if not farm_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cycle not found")
    return farm_id


async def require_cycle_permission(
    db: AsyncSession, user: CurrentUser, cycle_id: UUID | str, permission: str = "read"
) -> FarmAccess:
    farm_id = await farm_id_for_cycle(db, cycle_id)
    return await require_farm_permission(db, user, farm_id, permission)


async def farm_id_for_daily_log(db: AsyncSession, daily_log_id: UUID | str) -> UUID:
    result = await db.execute(
        select(Grid.farm_id)
        .join(Pond, Pond.grid_id == Grid.id)
        .join(Cycle, Cycle.pond_id == Pond.id)
        .join(DailyLog, DailyLog.cycle_id == Cycle.id)
        .where(DailyLog.id == daily_log_id)
    )
    farm_id = result.scalar_one_or_none()
    if not farm_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Daily log not found")
    return farm_id


async def require_daily_log_permission(
    db: AsyncSession, user: CurrentUser, daily_log_id: UUID | str, permission: str = "read"
) -> FarmAccess:
    farm_id = await farm_id_for_daily_log(db, daily_log_id)
    return await require_farm_permission(db, user, farm_id, permission)


async def _farm_id_for_child(
    db: AsyncSession, stmt: Select[tuple[UUID]], not_found: str
) -> UUID:
    result = await db.execute(stmt)
    farm_id = result.scalar_one_or_none()
    if not farm_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, not_found)
    return farm_id


async def require_feeding_permission(
    db: AsyncSession, user: CurrentUser, feeding_id: UUID, permission: str = "read"
) -> FarmAccess:
    farm_id = await _farm_id_for_child(
        db,
        select(Grid.farm_id)
        .join(Pond, Pond.grid_id == Grid.id)
        .join(Cycle, Cycle.pond_id == Pond.id)
        .join(DailyLog, DailyLog.cycle_id == Cycle.id)
        .join(FeedingSession, FeedingSession.daily_log_id == DailyLog.id)
        .where(FeedingSession.id == feeding_id),
        "Feeding not found",
    )
    return await require_farm_permission(db, user, farm_id, permission)


async def require_harvest_permission(
    db: AsyncSession, user: CurrentUser, harvest_id: UUID, permission: str = "read"
) -> FarmAccess:
    farm_id = await _farm_id_for_child(
        db,
        select(Grid.farm_id)
        .join(Pond, Pond.grid_id == Grid.id)
        .join(Cycle, Cycle.pond_id == Pond.id)
        .join(DailyLog, DailyLog.cycle_id == Cycle.id)
        .join(Harvest, Harvest.daily_log_id == DailyLog.id)
        .where(Harvest.id == harvest_id),
        "Harvest not found",
    )
    return await require_farm_permission(db, user, farm_id, permission)


async def require_treatment_permission(
    db: AsyncSession, user: CurrentUser, treatment_id: UUID, permission: str = "read"
) -> FarmAccess:
    farm_id = await _farm_id_for_child(
        db,
        select(Grid.farm_id)
        .join(Pond, Pond.grid_id == Grid.id)
        .join(Cycle, Cycle.pond_id == Pond.id)
        .join(DailyLog, DailyLog.cycle_id == Cycle.id)
        .join(Treatment, Treatment.daily_log_id == DailyLog.id)
        .where(Treatment.id == treatment_id),
        "Treatment not found",
    )
    return await require_farm_permission(db, user, farm_id, permission)


async def require_water_permission(
    db: AsyncSession, user: CurrentUser, water_id: UUID, permission: str = "read"
) -> FarmAccess:
    farm_id = await _farm_id_for_child(
        db,
        select(Grid.farm_id)
        .join(Pond, Pond.grid_id == Grid.id)
        .join(Cycle, Cycle.pond_id == Pond.id)
        .join(DailyLog, DailyLog.cycle_id == Cycle.id)
        .join(WaterParameters, WaterParameters.daily_log_id == DailyLog.id)
        .where(WaterParameters.id == water_id),
        "Water parameters not found",
    )
    return await require_farm_permission(db, user, farm_id, permission)
