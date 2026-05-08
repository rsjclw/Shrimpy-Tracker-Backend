from dataclasses import dataclass

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings


@dataclass
class CurrentUser:
    id: str
    email: str | None
    role: str


bearer_scheme = HTTPBearer(auto_error=True)

_jwks_cache: list[dict] | None = None


def _get_jwks() -> list[dict]:
    global _jwks_cache
    if _jwks_cache is None:
        url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        _jwks_cache = response.json()["keys"]
    return _jwks_cache


def _public_key_for(kid: str | None):
    """Return the public key matching the given kid, supporting both RSA and EC keys."""
    keys = _get_jwks()
    key_data = next((k for k in keys if k.get("kid") == kid), keys[0]) if kid else keys[0]

    kty = key_data.get("kty", "").upper()
    if kty == "EC":
        return jwt.algorithms.ECAlgorithm.from_jwk(key_data), ["ES256"]
    return jwt.algorithms.RSAAlgorithm.from_jwk(key_data), ["RS256"]


def _decode(token: str, kid: str | None) -> dict:
    public_key, algorithms = _public_key_for(kid)
    return jwt.decode(token, public_key, algorithms=algorithms, audience="authenticated")


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    global _jwks_cache
    token = credentials.credentials

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token header: {e}")

    kid = header.get("kid")

    try:
        payload = _decode(token, kid)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        # Keys may have rotated — clear cache and retry once
        _jwks_cache = None
        try:
            payload = _decode(token, kid)
        except jwt.ExpiredSignatureError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
        except jwt.InvalidTokenError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {e}")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing subject")

    return CurrentUser(
        id=user_id,
        email=payload.get("email"),
        role=payload.get("role", "authenticated"),
    )
