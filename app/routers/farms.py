from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import Farm, FarmMembership, User
from app.schemas import (
    FarmCreate,
    FarmDeleteOut,
    FarmMemberCreate,
    FarmMemberOut,
    FarmOut,
    FarmUpdate,
    RegisteredUserOut,
)
from app.services.access import (
    get_farm_access,
    is_admin,
    is_admin_email,
    list_memberships,
    normalize_email,
    require_admin,
    validate_role,
)
from app.services.users import registered_user_out

router = APIRouter(prefix="/farms", tags=["farms"])


def _empty_name_error() -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Farm name is required")


def _admin_member_error() -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, "Global admin emails do not need farm membership")


def _farm_out(farm: Farm, role: str) -> FarmOut:
    return FarmOut(id=farm.id, name=farm.name, created_at=farm.created_at, role=role)


def _farm_member_out(membership: FarmMembership) -> FarmMemberOut:
    return FarmMemberOut(
        farm_id=membership.farm_id,
        email=membership.email,
        user_id=membership.user_id,
        role=membership.role,
        created_at=membership.created_at,
    )


def _clean_farm_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise _empty_name_error()
    return cleaned


@router.get("", response_model=list[FarmOut])
async def list_farms(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[FarmOut]:
    if is_admin(user):
        result = await db.execute(select(Farm).order_by(Farm.name))
        return [_farm_out(farm, "admin") for farm in result.scalars().all()]

    memberships = await list_memberships(db, user)
    if not memberships:
        return []
    roles_by_farm = {membership.farm_id: membership.role for membership in memberships}
    result = await db.execute(
        select(Farm).where(Farm.id.in_(roles_by_farm.keys())).order_by(Farm.name)
    )
    return [
        _farm_out(farm, roles_by_farm[farm.id])
        for farm in result.scalars().all()
    ]


@router.post("", response_model=FarmOut, status_code=status.HTTP_201_CREATED)
async def create_farm(
    payload: FarmCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FarmOut:
    require_admin(user)
    farm = Farm(name=_clean_farm_name(payload.name))
    db.add(farm)
    await db.commit()
    await db.refresh(farm)
    return _farm_out(farm, "admin")


@router.get("/registered-users", response_model=list[RegisteredUserOut])
async def list_registered_users(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[RegisteredUserOut]:
    require_admin(user)
    result = await db.execute(select(User).order_by(User.created_at.desc(), User.email))
    return [registered_user_out(row) for row in result.scalars().all()]


@router.get("/{farm_id}", response_model=FarmOut)
async def get_farm(
    farm_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FarmOut:
    access = await get_farm_access(db, user, farm_id)
    if not access:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Farm access denied")
    farm = await db.get(Farm, farm_id)
    if not farm:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")
    return _farm_out(farm, access.role)


@router.put("/{farm_id}", response_model=FarmOut)
async def update_farm(
    farm_id: UUID,
    payload: FarmUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FarmOut:
    require_admin(user)
    farm = await db.get(Farm, farm_id)
    if not farm:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")
    farm.name = _clean_farm_name(payload.name)
    await db.commit()
    await db.refresh(farm)
    return _farm_out(farm, "admin")


@router.delete("/{farm_id}", response_model=FarmDeleteOut)
async def delete_farm(
    farm_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FarmDeleteOut:
    require_admin(user)
    farm = await db.get(Farm, farm_id)
    if not farm:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")
    farm_name = farm.name
    await db.delete(farm)
    await db.commit()
    return FarmDeleteOut(farm_id=farm_id, farm_name=farm_name, deleted=True)


@router.get("/{farm_id}/members", response_model=list[FarmMemberOut])
async def list_farm_members(
    farm_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[FarmMemberOut]:
    require_admin(user)
    farm = await db.get(Farm, farm_id)
    if not farm:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")
    result = await db.execute(
        select(FarmMembership)
        .where(FarmMembership.farm_id == farm_id)
        .order_by(FarmMembership.email)
    )
    return [
        _farm_member_out(membership)
        for membership in result.scalars().all()
        if not is_admin_email(membership.email)
    ]


@router.post("/{farm_id}/members", response_model=FarmMemberOut, status_code=status.HTTP_201_CREATED)
async def upsert_farm_member(
    farm_id: UUID,
    payload: FarmMemberCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FarmMemberOut:
    require_admin(user)
    farm = await db.get(Farm, farm_id)
    if not farm:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm not found")

    email = normalize_email(payload.email)
    if is_admin_email(email):
        raise _admin_member_error()
    role = validate_role(payload.role)
    result = await db.execute(
        select(FarmMembership).where(
            FarmMembership.farm_id == farm_id,
            func.lower(FarmMembership.email) == email,
        )
    )
    membership = result.scalar_one_or_none()
    if membership:
        membership.email = email
        membership.role = role
    else:
        membership = FarmMembership(farm_id=farm_id, email=email, role=role)
        db.add(membership)
    await db.commit()
    await db.refresh(membership)
    return _farm_member_out(membership)


@router.delete("/{farm_id}/members/{email}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_farm_member(
    farm_id: UUID,
    email: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    require_admin(user)
    result = await db.execute(
        select(FarmMembership).where(
            FarmMembership.farm_id == farm_id,
            func.lower(FarmMembership.email) == normalize_email(email),
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm member not found")
    await db.delete(membership)
    await db.commit()
