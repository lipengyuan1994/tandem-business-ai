"""P8-23: deterministic configuration, terminology, template and cookie contracts."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.main import create_app
from app.modules.notifications.templates import render, render_html
from app.modules.org.schemas import BusinessSettings
from app.platform.config import Settings, get_settings


@pytest.mark.parametrize("host,backup", [("", ""), ("host", " "), (" ", "backup"), ("x" * 201, "backup")])
def test_regulated_startup_refuses_missing_or_invalid_attestations(
    host: str, backup: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_PROFILE", "regulated")
    monkeypatch.setenv("REGULATED_HOST_BAA", host)
    monkeypatch.setenv("REGULATED_BACKUP_BAA", backup)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match=r"REGULATED_.*BAA|regulated_host_baa"):
            create_app()
    finally:
        get_settings.cache_clear()


def test_profiles_validate_without_exposing_secrets() -> None:
    settings = Settings(data_profile="regulated", regulated_host_baa=" host ", regulated_backup_baa=" backup ")
    assert settings.regulated_host_baa == "host"
    assert Settings(data_profile="standard", regulated_host_baa="", regulated_backup_baa="").data_profile == "standard"
    with pytest.raises(ValidationError) as exc:
        Settings(data_profile="unknown", app_secret_key="private-test-value-that-must-not-appear")
    assert "private-test-value" not in str(exc.value)


@pytest.mark.parametrize(
    "terms",
    [
        {"unknown": "Patient"},
        {"customer": ""},
        {"service": "x" * 41},
        {"customer": "<b>Patient</b>"},
        {"customer": "line\nbreak"},
        {"service": 7},
    ],
)
def test_terminology_rejects_invalid_values(terms: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BusinessSettings(terminology=terms)


def test_terminology_accepts_partial_plain_text_map() -> None:
    assert BusinessSettings(terminology={"customer": "Patient", "technicians": "Dentists"}).terminology == {
        "customer": "Patient",
        "technicians": "Dentists",
    }


@pytest.mark.parametrize("profile", ["standard", "regulated"])
@pytest.mark.parametrize("key", ["appointment_confirmation", "appointment_reminder"])
def test_appointment_templates_minimize_text(profile: str, key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", profile)
    variables = {
        "business_name": "Example Clinic",
        "local_time": "2027-01-11 10:00",
        "customer_name": "Ada Example",
        "service_name": "Private treatment",
        "resource_name": "Dr Example",
        "notes": "Personal notes",
    }
    subject, body = render(key, variables)
    html = render_html(key, variables)
    assert "Example Clinic" in str(subject) + body and "2027-01-11 10:00" in body
    if profile == "regulated":
        for value in ("Ada Example", "Private treatment", "Dr Example", "Personal notes"):
            assert value not in body + html + str(subject)
        assert "Reply to this message" in body
    else:
        assert "Private treatment" in body


def test_standard_view_audit_has_no_database_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    from app.platform.audit import record_view
    from app.platform.context import Context
    from app.platform.ids import new_id

    monkeypatch.setattr(get_settings(), "data_profile", "standard")
    db = Mock()
    record_view(
        db,
        Context(request_id="t", actor_kind="user", user_id=new_id(), business_id=new_id()),
        action="crm.customer.viewed",
        target_type="customer",
        target_id=new_id(),
    )
    assert db.mock_calls == []


@pytest.mark.parametrize("profile,seconds", [("standard", 43200), ("regulated", 28800)])
def test_staff_session_cookie_lifetime(profile: str, seconds: int, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import Response

    from app.modules.identity.api_auth import _set_cookie

    monkeypatch.setattr(get_settings(), "data_profile", profile)
    response = Response()
    _set_cookie(response, "synthetic-test-token")
    assert f"Max-Age={seconds}" in response.headers["set-cookie"]
