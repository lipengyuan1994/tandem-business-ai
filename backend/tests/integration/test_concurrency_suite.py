"""P3-15: cross-module races on the seeded demo data. Each race must yield exactly one success.

Single-module races live next to their module (approvals double click, same-slot booking, last-unit reservation).
These cover the same guarantees when the work goes through proposals, approvals and the worker together.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.identity.testing import login_as
from app.platform import clock, jobs
from app.platform.ids import fixture_id
from app.platform.registry import Registry
from app.seed import seed

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]
CYCLE = fixture_id("biz-cycle")


def _race(n: int, fn: Callable[[int], Any]) -> list[Any]:
    barrier, out, lock = threading.Barrier(n), [], threading.Lock()

    def run(i: int) -> None:
        barrier.wait()
        result = fn(i)
        with lock:
            out.append(result)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


@pytest.fixture
def shop(registry: Registry) -> dict[str, Any]:
    clock.set_clock(lambda: datetime(2026, 11, 5, 17, 30, tzinfo=UTC))
    seed()
    app = create_app(registry)
    operator = login_as(app, CYCLE, ["operator"])
    brake = operator.get("/api/v1/catalog/items", params={"q": "Brake service"}).json()["items"][0]
    customers = [
        operator.get("/api/v1/crm/customers/match", params={"name": n}).json()[0]["customer_id"]
        for n in ("Ada Example", "Dana Example")
    ]
    slots = operator.get(
        "/api/v1/scheduling/slots",
        params={
            "service_id": brake["id"],
            "earliest": "2026-11-10T16:00:00Z",
            "latest": "2026-11-11T00:00:00Z",
            "limit": 50,
        },
    ).json()
    return {"app": app, "operator": operator, "brake": brake, "customers": customers, "slots": slots}


def _propose_booking(shop: dict[str, Any], customer_id: str, slot: dict[str, Any]) -> dict[str, Any]:
    return dict(
        shop["operator"]
        .post(
            "/api/v1/approvals/proposals",
            json={
                "action_type": "scheduling.book_appointment",
                "title": "Book brake service",
                "payload": {
                    "customer": {"id": customer_id},
                    "service_id": shop["brake"]["id"],
                    "resource_id": slot["resource_id"],
                    "starts_at": slot["starts_at"],
                },
            },
        )
        .json()
    )


def _apart(slots: list[dict[str, Any]], gap: timedelta = timedelta(hours=2)) -> list[dict[str, Any]]:
    """Slots that cannot overlap each other (starts at least `gap` apart)."""
    picked: list[dict[str, Any]] = []
    for s in slots:
        if (
            not picked
            or datetime.fromisoformat(s["starts_at"]) - datetime.fromisoformat(picked[-1]["starts_at"]) >= gap
        ):
            picked.append(s)
    return picked


def _approve(client: TestClient, p: dict[str, Any]) -> Any:
    rev = p["current_revision"]
    return client.post(
        f"/api/v1/approvals/proposals/{p['id']}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )


def _pads(client: TestClient) -> dict[str, Any]:
    return dict(next(x for x in client.get("/api/v1/inventory/levels").json() if x["item_name"] == "Brake pads (pair)"))


def test_double_approval_of_booking_books_once(shop: dict[str, Any], registry: Registry, owner_engine: Engine) -> None:
    p = _propose_booking(shop, shop["customers"][0], shop["slots"][0])
    approvers = [login_as(shop["app"], CYCLE, ["owner"]) for _ in range(3)]
    codes = _race(3, lambda i: _approve(approvers[i], p).status_code)
    assert sorted(codes) == [200, 409, 409]
    jobs.run_pending(registry)
    with owner_engine.connect() as conn:
        booked = conn.execute(text("select count(*) from appointments where proposal_id = :p"), {"p": p["id"]}).scalar()
    assert booked == 1


def test_two_proposals_for_last_part_one_succeeds(shop: dict[str, Any], registry: Registry) -> None:
    owner = login_as(shop["app"], CYCLE, ["owner"])
    slots = _apart([s for s in shop["slots"] if s["resource_id"] == shop["slots"][0]["resource_id"]])
    # Use one unit directly so exactly one pair is left (seed: on hand 3, one already reserved).
    first = owner.post(
        "/api/v1/scheduling/appointments",
        json={
            "customer_id": shop["customers"][0],
            "service_id": shop["brake"]["id"],
            "resource_id": slots[0]["resource_id"],
            "starts_at": slots[0]["starts_at"],
        },
    )
    assert first.status_code == 201 and Decimal(_pads(owner)["available"]) == 1
    proposals = [_propose_booking(shop, shop["customers"][i], slots[1 + i]) for i in range(2)]
    for p in proposals:
        assert _approve(owner, p).status_code == 200
    _race(2, lambda _: jobs.run_pending(registry))  # two workers, one execution job each
    jobs.run_pending(registry)
    statuses = sorted(owner.get(f"/api/v1/approvals/proposals/{p['id']}").json()["status"] for p in proposals)
    assert statuses == ["failed", "succeeded"]
    failed = next(
        owner.get(f"/api/v1/approvals/proposals/{p['id']}").json()
        for p in proposals
        if owner.get(f"/api/v1/approvals/proposals/{p['id']}").json()["status"] == "failed"
    )
    assert failed["failure_code"] == "inventory.insufficient_stock"
    pads = _pads(owner)
    assert (Decimal(pads["on_hand"]), Decimal(pads["available"])) == (3, 0)


def test_proposal_and_direct_booking_for_same_slot_one_wins(shop: dict[str, Any], registry: Registry) -> None:
    owner = login_as(shop["app"], CYCLE, ["owner"])
    slot = shop["slots"][1]
    p = _propose_booking(shop, shop["customers"][0], slot)
    assert _approve(owner, p).status_code == 200

    def contender(i: int) -> Any:
        if i == 0:
            jobs.run_pending(registry)
            return owner.get(f"/api/v1/approvals/proposals/{p['id']}").json()["status"]
        return owner.post(
            "/api/v1/scheduling/appointments",
            json={
                "customer_id": shop["customers"][1],
                "service_id": shop["brake"]["id"],
                "resource_id": slot["resource_id"],
                "starts_at": slot["starts_at"],
            },
        ).status_code

    results = _race(2, contender)
    assert sorted(map(str, results)) in (["201", "failed"], ["409", "succeeded"])
