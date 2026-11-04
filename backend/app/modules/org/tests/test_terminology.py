"""Terminology persists in versioned business settings without crossing tenants."""

from __future__ import annotations

import uuid
from typing import Any


def test_terminology_round_trip_is_business_scoped(client: Any, cycle: uuid.UUID, garden: uuid.UUID) -> None:
    owner = client(cycle)
    current = owner.get("/api/v1/org/settings").json()
    changed = owner.patch(
        "/api/v1/org/settings",
        json={
            "version": current["version"],
            "settings": {**current["settings"], "terminology": {"customer": "Patient", "customers": "Patients"}},
        },
    )
    assert changed.status_code == 200, changed.text
    assert owner.get("/api/v1/org/settings").json()["settings"]["terminology"] == {
        "customer": "Patient",
        "customers": "Patients",
    }
    assert client(garden).get("/api/v1/org/settings").json()["settings"]["terminology"] == {}
    invalid = owner.patch(
        "/api/v1/org/settings",
        json={
            "version": changed.json()["version"],
            "settings": {"terminology": {"customer": "<script>"}},
        },
    )
    assert invalid.status_code == 422
    assert owner.get("/api/v1/org/settings").json() == changed.json()
