from uuid import uuid4

import jwt
import pytest

from app.config import settings
from app.security import (
    TEMP_PASSWORD_LENGTH,
    create_access_token,
    decode_access_token,
    generate_temp_password,
    hash_password,
    verify_password,
)
from app.services.users import new_user


def test_password_hash_round_trip():
    hashed = hash_password("correct horse")

    assert hashed != "correct horse"
    assert verify_password("correct horse", hashed)
    assert not verify_password("wrong horse", hashed)
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_temp_password_shape():
    password = generate_temp_password()

    assert len(password) == TEMP_PASSWORD_LENGTH
    assert password.isalnum()
    assert password != generate_temp_password()


def test_access_token_round_trip(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret")
    user_id = uuid4()

    payload = decode_access_token(create_access_token(user_id, "worker@example.com"))

    assert payload["sub"] == str(user_id)
    assert payload["email"] == "worker@example.com"
    assert payload["exp"] > payload["iat"]


def test_expired_token_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret")

    token = create_access_token(uuid4(), "worker@example.com", expires_hours=-1)

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


def test_token_signed_with_other_secret_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret")
    token = create_access_token(uuid4(), "worker@example.com")
    monkeypatch.setattr(settings, "jwt_secret_key", "another-secret")

    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token)


def test_new_user_generates_temp_password_when_missing():
    user, temporary_password = new_user("Worker@Example.com", None)

    assert user.email == "worker@example.com"
    assert temporary_password is not None
    assert user.must_change_password
    assert verify_password(temporary_password, user.password_hash)


def test_new_user_keeps_explicit_password():
    user, temporary_password = new_user("worker@example.com", "chosen-password")

    assert temporary_password is None
    assert not user.must_change_password
    assert verify_password("chosen-password", user.password_hash)
