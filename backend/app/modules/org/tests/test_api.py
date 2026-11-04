"""M02 org tests (docs/roadmap/modules/M02-org.md#tests-must-exist) plus the standard ones."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.platform.ids import new_id


def _default_location(client: Any) -> dict[str, Any]:
    return next(x for x in client.get("/api/v1/org/locations").json() if x["is_default"])


def test_bootstrap_creates_default_location_and_settings(client: Any, cycle: uuid.UUID) -> None:
    c = client(cycle)
    business = c.get("/api/v1/org/business").json()
    assert business["slug"] == "example-cycle-works" and business["timezone"] == "America/Denver"
    assert _default_location(c)["timezone"] == "America/Denver"
    settings = c.get("/api/v1/org/settings").json()
    assert settings["settings"]["hold_minutes"] == 30 and settings["settings"]["ai_policy"] == "hosted_vultr"


def test_invalid_timezone_rejected(client: Any, cycle: uuid.UUID) -> None:
    c = client(cycle)
    version = c.get("/api/v1/org/business").json()["version"]
    response = c.patch("/api/v1/org/business", json={"version": version, "timezone": "Mars/Olympus"})
    assert response.status_code == 422 and response.json()["errors"][0]["field"] == "timezone"


def test_update_business_version_conflict_and_audit(client: Any, cycle: uuid.UUID, owner_engine: Engine) -> None:
    c = client(cycle)
    version = c.get("/api/v1/org/business").json()["version"]
    ok = c.patch("/api/v1/org/business", json={"version": version, "name": "Example Cycle Works LLC"})
    assert ok.status_code == 200 and ok.json()["version"] == version + 1
    stale = c.patch("/api/v1/org/business", json={"version": version, "name": "Stale"})
    assert stale.status_code == 409 and stale.json()["current_version"] == version + 1
    with owner_engine.connect() as conn:
        changes = conn.execute(
            text("select changes from audit_events where action = 'org.business.updated'")
        ).scalar_one()
    assert changes == {"name": {"from": "Example Cycle Works", "to": "Example Cycle Works LLC"}}


def test_exactly_one_default_location(client: Any, cycle: uuid.UUID) -> None:
    c = client(cycle)
    first = _default_location(c)
    second = c.post("/api/v1/org/locations", json={"name": "Second shop", "is_default": True}).json()
    locations = c.get("/api/v1/org/locations").json()
    assert [x["id"] for x in locations if x["is_default"]] == [second["id"]]
    # the old default can't simply be switched off: a business must keep a default
    old = next(x for x in locations if x["id"] == first["id"])
    second_now = next(x for x in locations if x["id"] == second["id"])
    refused = c.patch(
        f"/api/v1/org/locations/{second['id']}", json={"version": second_now["version"], "is_default": False}
    )
    assert refused.status_code == 422 and refused.json()["code"] == "org.default_location_required"
    back = c.patch(f"/api/v1/org/locations/{first['id']}", json={"version": old["version"], "is_default": True})
    assert back.status_code == 200
    assert [x["id"] for x in c.get("/api/v1/org/locations").json() if x["is_default"]] == [first["id"]]


def test_opening_hours_replace_is_atomic_and_rejects_overlaps(client: Any, cycle: uuid.UUID) -> None:
    c = client(cycle)
    loc = _default_location(c)["id"]
    week = [{"weekday": d, "opens": "09:00", "closes": "17:00"} for d in range(1, 6)]
    assert len(c.put(f"/api/v1/org/locations/{loc}/opening-hours", json={"items": week}).json()) == 5
    overlap = [{"weekday": 1, "opens": "09:00", "closes": "12:00"}, {"weekday": 1, "opens": "11:00", "closes": "15:00"}]
    response = c.put(f"/api/v1/org/locations/{loc}/opening-hours", json={"items": overlap})
    assert response.status_code == 422 and response.json()["code"] == "org.invalid_hours"
    assert len(c.get(f"/api/v1/org/locations/{loc}/opening-hours").json()) == 5  # unchanged
    split = [{"weekday": 1, "opens": "09:00", "closes": "12:00"}, {"weekday": 1, "opens": "13:00", "closes": "17:00"}]
    assert len(c.put(f"/api/v1/org/locations/{loc}/opening-hours", json={"items": split}).json()) == 2


def test_ai_policy_change_requires_ai_settings_permission(client: Any, cycle: uuid.UUID) -> None:
    c = client(cycle)
    current = c.get("/api/v1/org/settings").json()
    body = {"version": current["version"], "settings": {**current["settings"], "ai_policy": "strict_private"}}
    assert c.patch("/api/v1/org/settings", json=body).status_code == 403
    allowed = client(cycle, {"org.settings.read", "org.settings.write", "ai.settings.write"})
    response = allowed.patch("/api/v1/org/settings", json=body)
    assert response.status_code == 200 and response.json()["settings"]["ai_policy"] == "strict_private"


def test_settings_reject_unknown_keys_and_out_of_range(client: Any, cycle: uuid.UUID) -> None:
    c = client(cycle)
    current = c.get("/api/v1/org/settings").json()
    bad = {"version": current["version"], "settings": {**current["settings"], "surprise": 1}}
    assert c.patch("/api/v1/org/settings", json=bad).status_code == 422
    bad = {"version": current["version"], "settings": {**current["settings"], "hold_minutes": 1}}
    assert c.patch("/api/v1/org/settings", json=bad).status_code == 422


def test_public_profile_resolves_by_slug_without_session(app_reg: Any, client: Any, cycle: uuid.UUID) -> None:
    from fastapi.testclient import TestClient

    c = client(cycle)
    loc = _default_location(c)["id"]
    c.put(
        f"/api/v1/org/locations/{loc}/opening-hours",
        json={"items": [{"weekday": 1, "opens": "09:00", "closes": "17:00"}]},
    )
    anonymous = TestClient(app_reg[0])
    profile = anonymous.get("/api/v1/public/example-cycle-works/profile").json()
    assert profile["name"] == "Example Cycle Works" and profile["opening_hours"][0]["opens"] == "09:00:00"
    assert profile["ai_policy"] == "hosted_vultr"  # default; the widget shows it in the privacy notice
    assert anonymous.get("/api/v1/public/nope-nope/profile").status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/v1/org/business", None),
        ("patch", "/api/v1/org/business", {"version": 1}),
        ("post", "/api/v1/org/locations", {"name": "X"}),
        ("get", "/api/v1/org/settings", None),
    ],
)
def test_denied_without_permission(client: Any, cycle: uuid.UUID, method: str, path: str, body: Any) -> None:
    response = getattr(client(cycle, set()), method)(path, **({"json": body} if body else {}))
    assert response.status_code == 403 and response.json()["code"] == "auth.permission_denied"


def test_cross_business_location_is_not_found(client: Any, cycle: uuid.UUID, garden: uuid.UUID) -> None:
    garden_location = _default_location(client(garden))
    c = client(cycle)
    assert c.get(f"/api/v1/org/locations/{garden_location['id']}/opening-hours").status_code == 404
    response = c.patch(f"/api/v1/org/locations/{garden_location['id']}", json={"version": 1, "name": "hijack"})
    assert response.status_code == 404
    assert c.get(f"/api/v1/org/locations/{new_id()}/opening-hours").status_code == 404


def test_slug_taken_and_invalid(cycle: uuid.UUID) -> None:
    from app.modules.org.tests.conftest import bootstrap
    from app.platform.errors import Conflict, Invalid

    with pytest.raises(Conflict):
        bootstrap("example-cycle-works")
    with pytest.raises(Invalid):
        bootstrap("Bad Slug!")
