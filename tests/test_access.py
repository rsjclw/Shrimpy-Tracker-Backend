from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.config import settings
from app.models import User
from app.routers.farms import _admin_member_error
from app.services.access import has_permission, is_admin, is_admin_email, require_admin, validate_role
from app.services.users import registered_user_out, user_out, validate_email


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


def test_require_admin_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com")

    require_admin(SimpleNamespace(email="boss@example.com"))
    with pytest.raises(HTTPException) as exc:
        require_admin(SimpleNamespace(email="worker@example.com"))

    assert exc.value.status_code == 403


def test_admin_email_is_not_a_farm_member(monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com")

    assert is_admin_email("boss@example.com")


def test_admin_member_error_is_bad_request():
    error = _admin_member_error()

    assert error.status_code == 400


def _user(email: str, **overrides) -> User:
    fields = {
        "id": UUID("11111111-1111-1111-1111-111111111111"),
        "email": email,
        "password_hash": "x",
        "must_change_password": False,
        "is_active": True,
        "created_at": datetime(2026, 5, 1, 10, tzinfo=timezone.utc),
        "last_login_at": None,
    }
    fields.update(overrides)
    return User(**fields)


def test_registered_user_out_marks_global_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com")

    out = registered_user_out(_user("boss@example.com", must_change_password=True))

    assert out.email == "boss@example.com"
    assert out.is_admin
    assert out.is_active
    assert out.must_change_password
    assert out.last_sign_in_at is None


def test_user_out_reports_admin_and_password_flag(monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com")

    assert user_out(_user("worker@example.com")).is_admin is False
    assert user_out(_user("boss@example.com")).is_admin is True


def test_validate_email_normalizes_and_rejects_garbage():
    assert validate_email("  Boss@Example.com ") == "boss@example.com"
    with pytest.raises(HTTPException) as exc:
        validate_email("not-an-email")
    assert exc.value.status_code == 422
