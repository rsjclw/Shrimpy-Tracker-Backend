import secrets
import string
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt

from app.config import settings

JWT_ALGORITHM = "HS256"
TEMP_PASSWORD_LENGTH = 12
_TEMP_PASSWORD_ALPHABET = string.ascii_letters + string.digits


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def generate_temp_password(length: int = TEMP_PASSWORD_LENGTH) -> str:
    return "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(length))


def create_access_token(user_id: UUID | str, email: str, expires_hours: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    hours = settings.jwt_expires_hours if expires_hours is None else expires_hours
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(hours=hours),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[JWT_ALGORITHM])
