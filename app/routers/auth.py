from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import User
from app.schemas import (
    ChangePasswordRequest,
    LoginOut,
    LoginRequest,
    RegisteredUserOut,
    ResetPasswordOut,
    ResetPasswordRequest,
    UserActiveUpdate,
    UserCreate,
    UserCreatedOut,
    UserOut,
)
from app.security import create_access_token, generate_temp_password, hash_password, verify_password
from app.services.access import normalize_email, require_admin
from app.services.login_throttle import login_throttle
from app.services.users import (
    get_user_by_email,
    get_user_or_404,
    new_user,
    registered_user_out,
    user_out,
    validate_email,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Compared against when the email is unknown so login timing does not reveal
# which emails exist.
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


def _invalid_credentials() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")


async def _current_db_user(db: AsyncSession, current: CurrentUser) -> User:
    try:
        user_id = UUID(current.id)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token subject")
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is not active")
    return user


@router.post("/login", response_model=LoginOut)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginOut:
    email = normalize_email(payload.email)
    ip = request.client.host if request.client else "unknown"
    retry_after = login_throttle.retry_after(email, ip)
    if retry_after:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = await get_user_by_email(db, email)
    password_hash = user.password_hash if user else _DUMMY_PASSWORD_HASH
    if not verify_password(payload.password, password_hash) or not user or not user.is_active:
        login_throttle.record_failure(email, ip)
        raise _invalid_credentials()

    login_throttle.clear(email)
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    return LoginOut(access_token=create_access_token(user.id, user.email), user=user_out(user))


@router.get("/me", response_model=UserOut)
async def me(
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> UserOut:
    return user_out(await _current_db_user(db, current))


@router.post("/change-password", response_model=UserOut)
async def change_password(
    payload: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> UserOut:
    user = await _current_db_user(db, current)
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    await db.commit()
    return user_out(user)


@router.post("/users", response_model=UserCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> UserCreatedOut:
    require_admin(current)
    email = validate_email(payload.email)
    if await get_user_by_email(db, email):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    user, temporary_password = new_user(email, payload.password)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserCreatedOut(user=registered_user_out(user), temporary_password=temporary_password)


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordOut)
async def reset_user_password(
    user_id: UUID,
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> ResetPasswordOut:
    require_admin(current)
    user = await get_user_or_404(db, user_id)
    password = payload.password
    temporary_password = None
    if not password:
        password = temporary_password = generate_temp_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    await db.commit()
    return ResetPasswordOut(temporary_password=temporary_password)


@router.patch("/users/{user_id}", response_model=RegisteredUserOut)
async def update_user(
    user_id: UUID,
    payload: UserActiveUpdate,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> RegisteredUserOut:
    require_admin(current)
    user = await get_user_or_404(db, user_id)
    if str(user.id) == current.id and not payload.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account")
    user.is_active = payload.is_active
    await db.commit()
    await db.refresh(user)
    return registered_user_out(user)
