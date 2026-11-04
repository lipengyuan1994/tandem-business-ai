"""M06 scheduling tests (docs/roadmap/modules/M06-scheduling.md#tests-must-exist)."""

from __future__ import annotations

import random
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.modules.scheduling import service
from app.modules.scheduling.schemas import BookingRequest
from app.platform import clock, events, jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict
from app.platform.registry import Registry

NOW = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)  # Thursday, as in data/fixtures.json
TUE = "2026-11-10"
WEEK = [{"weekday": d, "opens": "09:00", "closes": "17:00"} for d in range(1, 6)]  # Tue–Sat


def z(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class Shop:
    """The demo shop, built through the public APIs (same shape as the seed data)."""

    def __init__(self, app: Any, business: uuid.UUID) -> None:
        self.app, self.business = app, business
        self.owner = login_as(app, business, ["owner"])
        o = self.owner
        loc = next(x for x in o.get("/api/v1/org/locations").json() if x["is_default"])
        o.put(f"/api/v1/org/locations/{loc['id']}/opening-hours", json={"items": WEEK})
        self.pads = o.post(
            "/api/v1/catalog/items",
            json={"kind": "product", "sku": "BP-01", "name": "Brake pads (pair)", "is_stock_tracked": True},
        ).json()["id"]
        self.brake = o.post(
            "/api/v1/catalog/items",
            json={
                "kind": "service",
                "name": "Brake service",
                "duration_minutes": 60,
                "buffer_minutes": 15,
                "resource_kind": "staff",
                "price_minor": 6500,
            },
        ).json()["id"]
        self.tuneup = o.post(
            "/api/v1/catalog/items",
            json={
                "kind": "service",
                "name": "Tune-up",
                "duration_minutes": 90,
                "buffer_minutes": 15,
                "resource_kind": "staff",
            },
        ).json()["id"]
        o.put(f"/api/v1/catalog/items/{self.brake}/parts", json=[{"item_id": self.pads, "quantity": "1"}])
        o.post("/api/v1/inventory/movements", json={"item_id": self.pads, "quantity": "3", "reason": "receipt"})
        self.jordan = o.post("/api/v1/scheduling/resources", json={"name": "Jordan (technician)"}).json()["id"]
        o.put(
            f"/api/v1/scheduling/resources/{self.jordan}/availability",
            json=[{"weekday": d, "start_time": "09:00", "end_time": "17:00"} for d in range(1, 6)],
        )
        self.sky = o.post("/api/v1/scheduling/resources", json={"name": "Sky (contract)"}).json()["id"]
        o.put(
            f"/api/v1/scheduling/resources/{self.sky}/availability",
            json=[{"weekday": d, "start_time": "10:00", "end_time": "16:00"} for d in (3, 4, 5)],
        )
        self.ben = o.post("/api/v1/crm/customers", json={"display_name": "Ben Example"}).json()["id"]
        self.ada = o.post("/api/v1/crm/customers", json={"display_name": "Ada Example"}).json()["id"]

    def slots(self, service_id: str, earliest: str, latest: str, **kw: Any) -> list[dict[str, Any]]:
        r = self.owner.get(
            "/api/v1/scheduling/slots", params={"service_id": service_id, "earliest": earliest, "latest": latest, **kw}
        )
        assert r.status_code == 200, r.text
        return list(r.json())

    def book(self, service_id: str, customer: str, starts_at: str, resource: str | None = None, **kw: Any) -> Any:
        return self.owner.post(
            "/api/v1/scheduling/appointments",
            json={
                "customer_id": customer,
                "service_id": service_id,
                "resource_id": resource or self.jordan,
                "starts_at": starts_at,
                **kw,
            },
        )

    def available(self) -> Decimal:
        return Decimal(
            next(x for x in self.owner.get("/api/v1/inventory/levels").json() if x["item_id"] == self.pads)["available"]
        )

    def ctx(self) -> Context:
        return Context(
            request_id="t",
            actor_kind="user",
            user_id=self.owner.user_id,
            business_id=self.business,
            permissions=self.owner.permissions,
        )


@pytest.fixture
def shop(registry: Registry) -> Shop:
    clock.set_clock(lambda: NOW)
    return Shop(create_app(registry), make_business("example-cycle-works"))


def test_find_slots_respects_hours_rules_exceptions_buffer_and_lead_time(shop: Shop) -> None:
    assert (
        shop.book(shop.tuneup, shop.ben, f"{TUE}T17:00:00Z").status_code == 201
    )  # Tue 10:00–11:30 MST + 15 min buffer
    morning = shop.slots(shop.brake, f"{TUE}T16:00:00Z", f"{TUE}T19:00:00Z")  # 09:00–12:00 local
    assert [s["local_label"] for s in morning] == ["Tue 10 Nov 11:45 MST"]  # Sky doesn't work Tuesdays
    shop.owner.post(
        "/api/v1/scheduling/exceptions",
        json={
            "resource_id": shop.jordan,
            "starts_at": f"{TUE}T20:00:00Z",
            "ends_at": f"{TUE}T21:00:00Z",
            "kind": "closed",
            "reason": "dentist",
        },
    )
    afternoon = [
        s["local_label"][-9:] for s in shop.slots(shop.brake, f"{TUE}T19:00:00Z", f"{TUE}T23:59:00Z", limit=50)
    ]
    assert "13:00 MST" not in afternoon and "13:45 MST" not in afternoon and "14:00 MST" in afternoon
    assert afternoon[-1] == "15:45 MST"  # 15:45 + 60 + 15 = 17:00 closing
    today = shop.slots(shop.brake, "2026-11-05T17:30:00Z", "2026-11-05T23:59:00Z", limit=50)
    assert today[0]["starts_at"] >= "2026-11-05T18:30:00"  # 60 min lead time from 10:30 local


def test_find_slots_dst_transition_america_denver(shop: Shop) -> None:
    clock.set_clock(lambda: datetime(2026, 10, 27, 12, tzinfo=UTC))
    fri = shop.slots(shop.brake, "2026-10-30T06:00:00Z", "2026-10-31T06:00:00Z")  # Friday, still MDT (UTC−6)
    tue = shop.slots(shop.brake, "2026-11-03T07:00:00Z", "2026-11-04T07:00:00Z")  # Tuesday after fall-back, MST (UTC−7)
    assert fri[0]["starts_at"].startswith("2026-10-30T15:00") and fri[0]["local_label"].endswith("09:00 MDT")
    assert tue[0]["starts_at"].startswith("2026-11-03T16:00") and tue[0]["local_label"].endswith("09:00 MST")


def test_no_returned_slot_overlaps_existing_bookings(shop: Shop) -> None:
    rnd = random.Random(42)
    booked = []
    for _ in range(12):
        hour, minute = rnd.randint(16, 22), rnd.choice([0, 15, 30, 45])
        r = shop.book(
            shop.brake,
            shop.ben,
            f"{TUE}T{hour:02d}:{minute:02d}:00Z",
            resource=rnd.choice([shop.jordan]),
            override_hours=True,
        )
        if r.status_code == 201:
            booked.append((z(r.json()["starts_at"]), z(r.json()["blocked_until"])))
    for s in shop.slots(shop.brake, f"{TUE}T07:00:00Z", "2026-11-11T07:00:00Z", resource_id=shop.jordan, limit=100):
        start, end = z(s["starts_at"]), z(s["starts_at"]) + timedelta(minutes=75)
        assert all(end <= b0 or start >= b1 for b0, b1 in booked)


@pytest.mark.concurrency
def test_concurrent_bookings_same_slot_one_succeeds_one_conflict(shop: Shop) -> None:
    results: list[str] = []
    barrier = threading.Barrier(4)

    def attempt(customer: str) -> None:
        ctx = shop.ctx()
        barrier.wait()
        try:
            with transaction(ctx) as db:
                service.book(
                    db,
                    ctx,
                    BookingRequest(
                        customer_id=uuid.UUID(customer),
                        service_id=uuid.UUID(shop.brake),
                        resource_id=uuid.UUID(shop.jordan),
                        starts_at=z(f"{TUE}T16:00:00Z"),
                    ),
                )
            results.append("ok")
        except Conflict:
            results.append("conflict")

    threads = [threading.Thread(target=attempt, args=(c,)) for c in (shop.ben, shop.ada, shop.ben, shop.ada)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict", "conflict", "conflict", "ok"]
    assert shop.available() == 2  # exactly one reservation


def test_hold_blocks_slot_until_expiry_then_slot_free(shop: Shop) -> None:
    ctx = shop.ctx()
    with transaction(ctx) as db:
        service.place_hold(
            db,
            ctx,
            service_id=uuid.UUID(shop.brake),
            resource_id=uuid.UUID(shop.jordan),
            starts_at=z(f"{TUE}T16:00:00Z"),
            customer_id=uuid.UUID(shop.ada),
        )
    assert shop.slots(shop.brake, f"{TUE}T16:00:00Z", f"{TUE}T17:00:00Z") == []
    assert shop.book(shop.brake, shop.ben, f"{TUE}T16:00:00Z").json()["code"] == "scheduling.slot_unavailable"
    clock.set_clock(lambda: NOW + timedelta(minutes=31))
    shop.owner = login_as(shop.app, shop.business, ["owner"])  # fresh session after the clock jump
    assert shop.slots(shop.brake, f"{TUE}T16:00:00Z", f"{TUE}T17:00:00Z")[0]["starts_at"].startswith(f"{TUE}T16:00")
    assert (
        shop.book(shop.brake, shop.ben, f"{TUE}T16:00:00Z").status_code == 201
    )  # expired hold cancelled in the same transaction


def test_book_confirms_valid_hold_and_reserves_parts_atomically(shop: Shop) -> None:
    ctx = shop.ctx()
    with transaction(ctx) as db:
        hold = service.place_hold(
            db,
            ctx,
            service_id=uuid.UUID(shop.brake),
            resource_id=uuid.UUID(shop.jordan),
            starts_at=z(f"{TUE}T18:45:00Z"),
            customer_id=uuid.UUID(shop.ada),
        )
        hold_id = hold.id
    assert shop.available() == 3  # holds never reserve stock
    r = shop.book(shop.brake, shop.ada, f"{TUE}T18:45:00Z", hold_id=str(hold_id))
    assert r.status_code == 201 and r.json()["id"] == str(hold_id) and r.json()["status"] == "confirmed"
    assert shop.available() == 2


def test_booking_fails_entirely_when_part_insufficient(shop: Shop, owner_engine: Engine) -> None:
    shop.owner.post(
        "/api/v1/inventory/movements", json={"item_id": shop.pads, "quantity": "-3", "reason": "adjustment_loss"}
    )
    r = shop.book(shop.brake, shop.ada, f"{TUE}T16:00:00Z")
    assert r.status_code == 409 and r.json()["code"] == "inventory.insufficient_stock"
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from appointments")).scalar() == 0


def test_cancel_releases_reservation_without_stock_movement(shop: Shop, owner_engine: Engine) -> None:
    a = shop.book(shop.brake, shop.ada, f"{TUE}T16:00:00Z").json()
    assert shop.available() == 2
    c = shop.owner.post(
        f"/api/v1/scheduling/appointments/{a['id']}/cancel", json={"version": a["version"], "reason": "customer asked"}
    )
    assert c.json()["status"] == "cancelled" and shop.available() == 3
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from stock_movements")).scalar() == 1  # only the receipt
    assert shop.slots(shop.brake, f"{TUE}T16:00:00Z", f"{TUE}T17:00:00Z")[0]["starts_at"].startswith(f"{TUE}T16:00")


def test_complete_consumes_reservation_with_movement(shop: Shop) -> None:
    a = shop.book(shop.brake, shop.ada, f"{TUE}T16:00:00Z").json()
    done = shop.owner.post(f"/api/v1/scheduling/appointments/{a['id']}/complete", json={"version": a["version"]}).json()
    assert done["status"] == "completed"
    level = next(x for x in shop.owner.get("/api/v1/inventory/levels").json() if x["item_id"] == shop.pads)
    assert (Decimal(level["on_hand"]), Decimal(level["reserved"])) == (2, 0)


def test_reschedule_and_version_conflict(shop: Shop) -> None:
    a = shop.book(shop.brake, shop.ada, f"{TUE}T16:00:00Z").json()
    moved = shop.owner.post(
        f"/api/v1/scheduling/appointments/{a['id']}/reschedule",
        json={"version": a["version"], "starts_at": f"{TUE}T20:00:00Z"},
    )
    assert moved.status_code == 200 and moved.json()["starts_at"].startswith(f"{TUE}T20:00")
    stale = shop.owner.post(
        f"/api/v1/scheduling/appointments/{a['id']}/reschedule",
        json={"version": a["version"], "starts_at": f"{TUE}T21:30:00Z"},
    )
    assert stale.status_code == 409 and stale.json()["code"] == "scheduling.appointment_version_conflict"


@pytest.mark.parametrize("consent", [False, True])
def test_executor_new_customer_booking_and_reply_single_transaction(
    shop: Shop, registry: Registry, consent: bool
) -> None:
    visitor = TestClient(shop.app, base_url="https://testserver")
    conv = visitor.post(
        "/api/v1/public/example-cycle-works/chat",
        json={
            "name": "Dana",
            "email": "dana@example.com",
            "message": "Brakes squeak, Tuesday morning?",
            "consent_to_contact": consent,
        },
    ).json()
    operator = login_as(shop.app, shop.business, ["operator"])
    payload = {
        "customer": {
            "new": {"display_name": "Dana Example", "contact_points": [{"type": "email", "value": "dana@example.com"}]}
        },
        "service_id": shop.brake,
        "resource_id": shop.jordan,
        "starts_at": f"{TUE}T18:45:00Z",
        "reply": {"conversation_id": conv["conversation_id"], "body": "Booked: Tue 10 Nov 11:45. See you!"},
    }
    p = operator.post(
        "/api/v1/approvals/proposals",
        json={"action_type": "scheduling.book_appointment", "title": "Book Dana", "payload": payload},
    ).json()
    labels = [line["label"] for line in p["current_revision"]["preview"]]
    assert labels == ["New customer", "Appointment", "Reserve Brake pads (pair)", "Reply to customer"]
    assert p["current_revision"]["preview"][1]["after"].startswith("Tue 10 Nov 11:45 MST")
    rev = p["current_revision"]
    shop.owner.post(
        f"/api/v1/approvals/proposals/{p['id']}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    jobs.run_pending(registry)
    detail = shop.owner.get(f"/api/v1/approvals/proposals/{p['id']}").json()
    assert detail["status"] == "succeeded", detail
    result = detail["attempts"][0]["result"]
    appt = shop.owner.get(f"/api/v1/scheduling/appointments/{result['appointment_id']}").json()
    assert appt["customer_name"] == "Dana Example" and appt["status"] == "confirmed" and appt["source"] == "assistant"
    assert shop.available() == 2
    thread = visitor.get("/api/v1/public/example-cycle-works/chat").json()["messages"]
    assert thread[-1] == {**thread[-1], "from_business": True, "body": "Booked: Tue 10 Nov 11:45. See you!"}
    timeline = shop.owner.get(f"/api/v1/crm/customers/{result['customer_id']}/timeline").json()
    assert {t["kind"] for t in timeline} == {"appointment", "message"}
    records = shop.owner.get(f"/api/v1/crm/customers/{result['customer_id']}/consents").json()
    assert len(records) == int(consent)
    if consent:
        assert (records[0]["channel"], records[0]["purpose"], records[0]["status"], records[0]["source"]) == (
            "email",
            "service_updates",
            "granted",
            "form",
        )
        assert datetime.fromisoformat(records[0]["recorded_at"]) == NOW
        assert f"conversation {conv['conversation_id']}" in records[0]["evidence"]


def test_executor_fails_cleanly_when_slot_taken_meanwhile(shop: Shop, registry: Registry, owner_engine: Engine) -> None:
    operator = login_as(shop.app, shop.business, ["operator"])
    p = operator.post(
        "/api/v1/approvals/proposals",
        json={
            "action_type": "scheduling.book_appointment",
            "title": "Book Ada",
            "payload": {
                "customer": {"id": shop.ada},
                "service_id": shop.brake,
                "resource_id": shop.jordan,
                "starts_at": f"{TUE}T16:00:00Z",
            },
        },
    ).json()
    shop.book(shop.brake, shop.ben, f"{TUE}T16:00:00Z")  # someone books the slot by phone first
    rev = p["current_revision"]
    shop.owner.post(
        f"/api/v1/approvals/proposals/{p['id']}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    jobs.run_pending(registry)
    detail = shop.owner.get(f"/api/v1/approvals/proposals/{p['id']}").json()
    assert detail["status"] == "failed" and detail["failure_code"] == "scheduling.slot_unavailable"
    assert shop.available() == 2  # only Ben's reservation


def test_holds_released_when_proposal_rejected(shop: Shop, registry: Registry, owner_engine: Engine) -> None:
    operator = login_as(shop.app, shop.business, ["operator"])
    p = operator.post(
        "/api/v1/approvals/proposals",
        json={"action_type": "testing.noop_write", "title": "x", "payload": {"name": "x"}},
    ).json()
    ctx = shop.ctx()
    with transaction(ctx) as db:
        service.place_hold(
            db,
            ctx,
            service_id=uuid.UUID(shop.brake),
            resource_id=uuid.UUID(shop.jordan),
            starts_at=z(f"{TUE}T16:00:00Z"),
            customer_id=uuid.UUID(shop.ada),
            proposal_id=uuid.UUID(p["id"]),
        )
    rev = p["current_revision"]
    shop.owner.post(
        f"/api/v1/approvals/proposals/{p['id']}/decisions",
        json={"decision": "reject", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    events.deliver_pending(registry)
    with owner_engine.connect() as conn:
        assert conn.execute(text("select status, cancelled_reason from appointments")).one() == (
            "cancelled",
            "proposal rejected",
        )


def test_bind_hold_then_reject_releases_hold(shop: Shop, registry: Registry, owner_engine: Engine) -> None:
    from app.modules.scheduling import public as scheduling

    operator = login_as(shop.app, shop.business, ["operator"])
    p = operator.post(
        "/api/v1/approvals/proposals",
        json={"action_type": "testing.noop_write", "title": "x", "payload": {"name": "x"}},
    ).json()
    ctx = shop.ctx()
    with transaction(ctx) as db:
        hold = scheduling.place_hold(
            db,
            ctx,
            service_id=uuid.UUID(shop.brake),
            resource_id=uuid.UUID(shop.jordan),
            starts_at=z(f"{TUE}T16:00:00Z"),
            customer_id=uuid.UUID(shop.ada),
        )
        scheduling.bind_hold(db, ctx, hold.hold_id, uuid.UUID(p["id"]))
        scheduling.bind_hold(db, ctx, hold.hold_id, uuid.UUID(p["id"]))
    with owner_engine.connect() as conn:
        assert conn.execute(text("select proposal_id from appointments")).scalar_one() == uuid.UUID(p["id"])
    rev = p["current_revision"]
    rejected = shop.owner.post(
        f"/api/v1/approvals/proposals/{p['id']}/decisions",
        json={"decision": "reject", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    assert rejected.status_code == 200
    events.deliver_pending(registry)
    with owner_engine.connect() as conn:
        assert conn.execute(text("select status, cancelled_reason from appointments")).one() == (
            "cancelled",
            "proposal rejected",
        )


def test_bind_hold_rejects_other_business_or_bound_hold(shop: Shop) -> None:
    from app.modules.approvals import public as approvals
    from app.modules.scheduling import public as scheduling
    from app.platform.ids import new_id
    from app.platform.testing.auth import ctx_for

    ctx = shop.ctx()
    with transaction(ctx) as db:
        proposal = approvals.create_proposal(
            db, ctx, action_type="testing.noop_write", payload={"name": "x"}, title="Binding target"
        ).id
        hold = scheduling.place_hold(
            db,
            ctx,
            service_id=uuid.UUID(shop.brake),
            resource_id=uuid.UUID(shop.jordan),
            starts_at=z(f"{TUE}T16:00:00Z"),
            customer_id=uuid.UUID(shop.ada),
        )
        scheduling.bind_hold(db, ctx, hold.hold_id, proposal)
        with pytest.raises(Conflict, match=r"scheduling\.hold_not_bindable"):
            scheduling.bind_hold(db, ctx, hold.hold_id, new_id())
        with pytest.raises(Conflict, match=r"scheduling\.hold_not_bindable"):
            scheduling.bind_hold(db, ctx, new_id(), proposal)
    other = ctx_for(make_business("other-shop"), ["scheduling.appointment.write"])
    with transaction(other) as db, pytest.raises(Conflict, match=r"scheduling\.hold_not_bindable"):
        scheduling.bind_hold(db, other, hold.hold_id, proposal)
    with transaction(ctx) as db:
        db.execute(text("update appointments set status='cancelled' where id=:i"), {"i": hold.hold_id})
        with pytest.raises(Conflict, match=r"scheduling\.hold_not_bindable"):
            scheduling.bind_hold(db, ctx, hold.hold_id, proposal)


def test_bind_hold_refreshes_previously_loaded_hold(shop: Shop) -> None:
    from app.modules.approvals import public as approvals
    from app.modules.scheduling import public as scheduling
    from app.modules.scheduling.models import Appointment
    from app.platform.ids import new_id

    ctx = shop.ctx()
    with transaction(ctx) as db:
        proposal = approvals.create_proposal(
            db, ctx, action_type="testing.noop_write", payload={"name": "x"}, title="Binding target"
        ).id
        hold = scheduling.place_hold(
            db,
            ctx,
            service_id=uuid.UUID(shop.brake),
            resource_id=uuid.UUID(shop.jordan),
            starts_at=z(f"{TUE}T16:00:00Z"),
            customer_id=uuid.UUID(shop.ada),
        )
    with transaction(ctx) as db:
        stale = db.get(Appointment, hold.hold_id)
        assert stale is not None and stale.proposal_id is None
        with transaction(ctx) as other:
            scheduling.bind_hold(other, ctx, hold.hold_id, proposal)
        with pytest.raises(Conflict, match=r"scheduling\.hold_not_bindable"):
            scheduling.bind_hold(db, ctx, hold.hold_id, new_id())
        assert stale.proposal_id == proposal


@pytest.mark.parametrize("missing", [False, True])
def test_bind_hold_rejects_foreign_or_missing_proposal(shop: Shop, missing: bool, owner_engine: Engine) -> None:
    from app.modules.approvals import public as approvals
    from app.modules.org.testing import system_ctx
    from app.modules.scheduling import public as scheduling
    from app.platform.ids import new_id

    other_business = make_business("other-shop")
    other_ctx = system_ctx(other_business, frozenset({"approvals.proposal.create"}))
    with transaction(other_ctx) as db:
        foreign = approvals.create_proposal(
            db, other_ctx, action_type="testing.noop_write", payload={"name": "x"}, title="Other shop proposal"
        ).id
    ctx = shop.ctx()
    with transaction(ctx) as db:
        hold = scheduling.place_hold(
            db,
            ctx,
            service_id=uuid.UUID(shop.brake),
            resource_id=uuid.UUID(shop.jordan),
            starts_at=z(f"{TUE}T16:00:00Z"),
            customer_id=uuid.UUID(shop.ada),
        )
        with pytest.raises(Conflict, match=r"scheduling\.hold_not_bindable"):
            scheduling.bind_hold(db, ctx, hold.hold_id, new_id() if missing else foreign)
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("select proposal_id from appointments where id=:i"), {"i": hold.hold_id}).scalar_one()
            is None
        )


def test_appointment_request_creates_conversation_not_appointment(shop: Shop, owner_engine: Engine) -> None:
    item = shop.owner.get(f"/api/v1/catalog/items/{shop.brake}").json()
    shop.owner.patch(
        f"/api/v1/catalog/items/{shop.brake}", json={"version": item["version"], "is_bookable_online": True}
    )
    visitor = TestClient(shop.app, base_url="https://testserver")
    r = visitor.post(
        "/api/v1/public/example-cycle-works/appointment-requests",
        json={
            "service_id": shop.brake,
            "name": "Eve",
            "email": "eve@example.com",
            "preferred": [{"date": TUE, "time_from": "09:00", "time_to": "12:00"}],
        },
    )
    assert r.status_code == 201 and r.json()["received"] is True
    inbox = shop.owner.get("/api/v1/conversations").json()["items"]
    assert (
        inbox[0]["subject"] == "Request: Brake service" and "Tue 10 Nov 09:00–12:00" in inbox[0]["last_message_preview"]
    )
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from appointments")).scalar() == 0


def test_permissions_and_isolation(shop: Shop) -> None:
    a = shop.book(shop.brake, shop.ada, f"{TUE}T16:00:00Z").json()
    auditor = login_as(shop.app, shop.business, ["auditor"])
    assert (
        auditor.get(
            "/api/v1/scheduling/appointments", params={"start": f"{TUE}T00:00:00Z", "end": "2026-11-11T00:00:00Z"}
        ).status_code
        == 200
    )
    assert auditor.post("/api/v1/scheduling/appointments", json={}).status_code in (403, 422)
    assert (
        shop.book(shop.brake, shop.ada, f"{TUE}T23:30:00Z", override_hours=True).status_code == 201
    )  # owner may override hours
    op = login_as(shop.app, shop.business, ["operator"])
    assert (
        op.post(
            "/api/v1/scheduling/appointments",
            json={
                "customer_id": shop.ada,
                "service_id": shop.brake,
                "resource_id": shop.jordan,
                "starts_at": f"{TUE}T22:00:00Z",
                "override_hours": True,
            },
        ).status_code
        == 403
    )
    other = login_as(shop.app, make_business("example-garden-store"), ["owner"])
    assert other.get(f"/api/v1/scheduling/appointments/{a['id']}").status_code == 404


def test_confirming_a_hold_blocks_the_current_service_duration(shop: Shop, owner_engine: Engine) -> None:
    """Review finding: a hold keeps the interval it was placed with; confirming must use the service's current one."""
    ctx = shop.ctx()
    with transaction(ctx) as db:
        hold_id = service.place_hold(
            db,
            ctx,
            service_id=uuid.UUID(shop.brake),
            resource_id=uuid.UUID(shop.jordan),
            starts_at=z(f"{TUE}T18:45:00Z"),  # held until 19:45 + 15 min buffer = 20:00
            customer_id=uuid.UUID(shop.ada),
        ).id
    item = shop.owner.get(f"/api/v1/catalog/items/{shop.brake}").json()
    changed = shop.owner.patch(
        f"/api/v1/catalog/items/{shop.brake}", json={"version": item["version"], "duration_minutes": 120}
    )
    assert changed.status_code == 200
    # Someone books the time right after the old hold interval, which the longer service would now overlap.
    assert shop.book(shop.tuneup, shop.ben, f"{TUE}T20:00:00Z").status_code == 201
    r = shop.book(shop.brake, shop.ada, f"{TUE}T18:45:00Z", hold_id=str(hold_id))
    assert r.status_code == 409 and r.json()["code"] == "scheduling.slot_unavailable"
    with owner_engine.connect() as conn:
        status = conn.execute(text("select status from appointments where id = :i"), {"i": str(hold_id)}).scalar()
    assert status == "held"  # nothing was half-applied


def test_reschedule_into_a_slot_with_an_expired_hold(shop: Shop) -> None:
    """Review finding: the minute-based cleanup job may not have run yet; rescheduling must not hit the constraint."""
    a = shop.book(shop.brake, shop.ben, f"{TUE}T16:00:00Z").json()
    ctx = shop.ctx()
    with transaction(ctx) as db:
        service.place_hold(
            db,
            ctx,
            service_id=uuid.UUID(shop.brake),
            resource_id=uuid.UUID(shop.jordan),
            starts_at=z(f"{TUE}T18:45:00Z"),
            customer_id=uuid.UUID(shop.ada),
        )
    clock.set_clock(lambda: NOW + timedelta(minutes=31))  # the hold has expired; no cleanup job has run
    shop.owner = login_as(shop.app, shop.business, ["owner"])
    moved = shop.owner.post(
        f"/api/v1/scheduling/appointments/{a['id']}/reschedule",
        json={"version": a["version"], "starts_at": f"{TUE}T18:45:00Z"},
    )
    assert moved.status_code == 200, moved.text


def test_booking_a_service_with_a_very_long_name_still_writes_the_timeline(shop: Shop) -> None:
    """Review finding: timeline summaries are limited to 200 characters; service names can be 200 long."""
    name = "Long " * 40
    long = shop.owner.post(
        "/api/v1/catalog/items",
        json={"kind": "service", "name": name.strip(), "duration_minutes": 30, "resource_kind": "staff"},
    ).json()["id"]
    assert shop.book(long, shop.ben, f"{TUE}T16:00:00Z").status_code == 201
    timeline = shop.owner.get(f"/api/v1/crm/customers/{shop.ben}/timeline").json()
    assert any(len(t["summary"]) <= 200 and "booked" in t["summary"] for t in timeline)


@pytest.mark.parametrize(
    "instant, expected",
    [
        ("2026-11-01T06:05:00Z", "2026-11-01T06:15:00Z"),
        ("2026-11-01T06:15:01Z", "2026-11-01T06:30:00Z"),
        ("2026-11-01T05:55:00Z", "2026-11-01T07:00:00Z"),
    ],
)
def test_slot_rounding_preserves_dst_occurrence(instant: str, expected: str) -> None:
    from zoneinfo import ZoneInfo

    from app.modules.scheduling.availability import align_up, slot_starts

    start = z(instant)
    tz = ZoneInfo("America/New_York")
    assert align_up(start, tz, 15) == z(expected)
    slots = slot_starts(
        [(start, z("2026-11-01T08:00:00Z"))],
        tz=tz,
        granularity_min=15,
        block=timedelta(minutes=15),
        not_before=start,
        latest_start=z("2026-11-01T08:00:00Z"),
    )
    assert slots and slots[0] == z(expected) and all(s >= start for s in slots)


def _request_appointment(visitor: TestClient, service_id: str) -> Any:
    return visitor.post(
        "/api/v1/public/example-cycle-works/appointment-requests",
        json={
            "service_id": service_id,
            "name": "Eve Example",
            "email": "eve@example.com",
            "preferred": [{"date": TUE, "time_from": "09:00", "time_to": "12:00"}],
        },
    )


def _online_service(shop: Shop, **changes: Any) -> str:
    response = shop.owner.post(
        "/api/v1/catalog/items",
        json={
            "kind": "service",
            "name": "Online service",
            "duration_minutes": 60,
            "resource_kind": "staff",
            "is_bookable_online": True,
            **changes,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_appointment_request_rejects_disabled_intake(shop: Shop) -> None:
    service_id = _online_service(shop)
    settings = shop.owner.get("/api/v1/org/settings").json()
    shop.owner.patch(
        "/api/v1/org/settings",
        json={"version": settings["version"], "settings": {**settings["settings"], "public_chat_enabled": False}},
    )
    assert _request_appointment(TestClient(shop.app, base_url="https://testserver"), service_id).status_code == 403
    assert shop.owner.get("/api/v1/conversations").json()["items"] == []


def test_appointment_request_rechecks_online_eligibility(shop: Shop) -> None:
    service_id = _online_service(shop)
    item = shop.owner.get(f"/api/v1/catalog/items/{service_id}").json()
    r = shop.owner.patch(
        f"/api/v1/catalog/items/{service_id}", json={"version": item["version"], "is_bookable_online": False}
    )
    assert r.status_code == 200, r.text
    assert _request_appointment(TestClient(shop.app, base_url="https://testserver"), service_id).status_code == 404
    assert shop.owner.get("/api/v1/conversations").json()["items"] == []


def test_appointment_request_returns_access_to_its_reply_thread(shop: Shop) -> None:
    v = TestClient(shop.app, base_url="https://testserver")
    original = v.post("/api/v1/public/example-cycle-works/chat", json={"name": "Eve", "message": "Old chat"}).json()
    response = _request_appointment(v, _online_service(shop))
    assert response.status_code == 201, response.text
    assert response.json()["reply_url"] == "/p/example-cycle-works/chat"
    thread = v.get("/api/v1/public/example-cycle-works/chat")
    assert thread.status_code == 200
    cid = thread.json()["conversation_id"]
    assert cid != original["conversation_id"]
    assert "Appointment request:" in thread.json()["messages"][0]["body"]
    reply = shop.owner.post(
        f"/api/v1/conversations/{cid}/messages", json={"direction": "outbound", "body": "Tuesday at ten works"}
    )
    assert reply.status_code == 201
    assert v.get("/api/v1/public/example-cycle-works/chat").json()["messages"][-1]["body"] == "Tuesday at ten works"


def test_appointment_request_bounds_generated_subject(shop: Shop) -> None:
    service_id = _online_service(shop, name="S" * 200)
    response = _request_appointment(TestClient(shop.app, base_url="https://testserver"), service_id)
    assert response.status_code == 201, response.text
    assert shop.owner.get("/api/v1/conversations").json()["items"][0]["subject"] == ("Request: " + "S" * 200)[:200]


def test_public_creation_budget_shared_across_businesses_and_entrypoints(shop: Shop) -> None:
    v = TestClient(shop.app, base_url="https://testserver")
    service_id = _online_service(shop)
    for _ in range(3):
        assert _request_appointment(v, service_id).status_code == 201
    make_business("example-garden-store")
    for _ in range(2):
        assert (
            v.post("/api/v1/public/example-garden-store/chat", json={"name": "Example", "message": "Hello"}).status_code
            == 201
        )
    assert (
        v.post("/api/v1/public/example-cycle-works/chat", json={"name": "Example", "message": "Hello"}).status_code
        == 429
    )
    assert _request_appointment(v, service_id).status_code == 429


def test_booking_reserves_parts_in_item_order(shop: Shop, monkeypatch: Any) -> None:
    from app.modules.inventory import public as inventory

    low = shop.pads
    high = shop.owner.post(
        "/api/v1/catalog/items", json={"kind": "product", "name": "AAA first by name", "is_stock_tracked": True}
    ).json()["id"]
    shop.owner.post("/api/v1/inventory/movements", json={"item_id": high, "quantity": "3", "reason": "receipt"})
    shop.owner.put(
        f"/api/v1/catalog/items/{shop.brake}/parts",
        json=[{"item_id": low, "quantity": "1"}, {"item_id": high, "quantity": "1"}],
    )
    seen: list[str] = []
    reserve = inventory.reserve_stock

    def capture(*args: Any, **kw: Any) -> Any:
        seen.append(str(kw["item_id"]))
        return reserve(*args, **kw)

    monkeypatch.setattr(inventory, "reserve_stock", capture)
    result = shop.book(shop.brake, shop.ada, f"{TUE}T16:00:00Z")
    assert result.status_code == 201, result.text
    assert seen == sorted([low, high])


def test_appointment_cancellation_locks_customer_before_stock(shop: Shop, owner_engine: Engine) -> None:
    from sqlalchemy import event

    from app.modules.org.testing import system_ctx
    from app.platform.db import get_engine

    appointment = shop.book(shop.brake, shop.ada, f"{TUE}T16:00:00Z").json()
    entered = threading.Event()
    errors: list[Exception] = []
    ctx = system_ctx(shop.business, frozenset({"scheduling.appointment.write"}))

    def before_execute(conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool) -> None:
        if threading.current_thread().name == "cancel-appointment" and (
            "pg_advisory_xact_lock" in statement or statement.startswith("UPDATE customers")
        ):
            entered.set()

    def cancel() -> None:
        try:
            with transaction(ctx) as db:
                service.cancel(db, ctx, uuid.UUID(appointment["id"]), appointment["version"], "Synthetic cancellation")
        except Exception as exc:
            errors.append(exc)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", before_execute)
    thread = threading.Thread(target=cancel, name="cancel-appointment")
    try:
        with owner_engine.begin() as holder:
            holder.execute(text("select id from customers where id=:i for update"), {"i": shop.ada})
            thread.start()
            assert entered.wait(5)
            holder.execute(text("select id from stock_levels where item_id=:i for update nowait"), {"i": shop.pads})
        thread.join(5)
        assert not thread.is_alive() and not errors, errors
    finally:
        thread.join(5)
        event.remove(engine, "before_cursor_execute", before_execute)
