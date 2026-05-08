from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import settings
from app.routers.farms import (
    _admin_member_error,
    _fetch_registered_users,
    _is_admin_email,
    _registered_user_out,
    _sort_registered_users,
)
from app.services.access import has_permission, is_admin, validate_role


def test_role_permissions_match_hierarchy():
    assert has_permission("owner", "read")
    assert has_permission("owner", "add")
    assert has_permission("owner", "manage")

    assert has_permission("admin", "read")
    assert has_permission("admin", "add")
    assert has_permission("admin", "manage")

    assert has_permission("operator", "read")
    assert has_permission("operator", "add")
    assert not has_permission("operator", "manage")

    assert has_permission("viewer", "read")
    assert not has_permission("viewer", "add")
    assert not has_permission("viewer", "manage")


def test_invalid_role_is_rejected():
    with pytest.raises(HTTPException):
        validate_role("admin")


def test_admin_email_is_global_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com,other@example.com")

    assert is_admin(SimpleNamespace(email="Boss@Example.com"))
    assert not is_admin(SimpleNamespace(email="worker@example.com"))


def test_admin_email_is_not_a_farm_member(monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com")

    assert _is_admin_email("boss@example.com")


def test_admin_member_error_is_bad_request():
    error = _admin_member_error()

    assert error.status_code == 400


def test_registered_user_out_marks_global_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com")

    user = _registered_user_out(
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "email": "Boss@Example.com",
            "created_at": "2026-05-01T10:00:00Z",
            "last_sign_in_at": None,
        }
    )

    assert user is not None
    assert user.email == "boss@example.com"
    assert user.is_admin


def test_registered_users_sort_newest_first():
    older = _registered_user_out(
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "email": "older@example.com",
            "created_at": "2026-05-01T10:00:00Z",
        }
    )
    newer = _registered_user_out(
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "email": "newer@example.com",
            "created_at": "2026-05-03T10:00:00Z",
        }
    )

    assert [user.email for user in _sort_registered_users([older, newer])] == [
        "newer@example.com",
        "older@example.com",
    ]


@pytest.mark.asyncio
async def test_fetch_registered_users_requires_service_role_key(monkeypatch):
    monkeypatch.setattr(settings, "supabase_service_role_key", "")

    with pytest.raises(HTTPException) as exc:
        await _fetch_registered_users()

    assert exc.value.status_code == 503
