from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.schemas import RegisteredUserOut, UserOut
from app.security import generate_temp_password, hash_password
from app.services.access import is_admin_email, normalize_email


def validate_email(email: str) -> str:
    normalized = normalize_email(email)
    if "@" not in normalized or len(normalized) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid email address")
    return normalized


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        is_admin=is_admin_email(user.email),
        must_change_password=user.must_change_password,
    )


def registered_user_out(user: User) -> RegisteredUserOut:
    return RegisteredUserOut(
        id=user.id,
        email=user.email,
        created_at=user.created_at,
        last_sign_in_at=user.last_login_at,
        is_admin=is_admin_email(user.email),
        is_active=user.is_active,
        must_change_password=user.must_change_password,
    )


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def get_user_or_404(db: AsyncSession, user_id: UUID) -> User:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


def new_user(
    email: str,
    password: str | None,
    *,
    must_change_password: bool | None = None,
    user_id: UUID | None = None,
    created_at: datetime | None = None,
) -> tuple[User, str | None]:
    """Build an unsaved User. Returns the generated temporary password when none was given."""
    temporary_password = None
    if not password:
        password = temporary_password = generate_temp_password()
    if must_change_password is None:
        must_change_password = temporary_password is not None
    user = User(
        email=normalize_email(email),
        password_hash=hash_password(password),
        must_change_password=must_change_password,
    )
    if user_id is not None:
        user.id = user_id
    if created_at is not None:
        user.created_at = created_at
    return user, temporary_password
