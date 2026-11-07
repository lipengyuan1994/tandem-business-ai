"""P10-E1 resource allocation and database race regression tests."""

import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.modules.scheduling import lifecycle, service
from app.modules.scheduling.models import AppointmentResource
from app.modules.scheduling.schemas import BookingRequest
from app.modules.scheduling.tests.test_scheduling import Shop, z
from app.modules.scheduling.tests.test_scheduling import shop as shop
from app.platform.db import transaction
from app.platform.errors import Conflict


@pytest.fixture
def dental(shop: Shop):
    o = shop.owner
    response = o.patch(
        f"/api/v1/catalog/items/{shop.brake}",
        json={
            "version": 1,
            "required_resources": [{"role": "provider", "kind": "staff"}, {"role": "chair", "kind": "room"}],
        },
    )
    assert response.status_code == 200, response.text
    chair = o.post("/api/v1/scheduling/resources", json={"name": "Chair 1", "kind": "room"}).json()["id"]
    o.put(
        f"/api/v1/scheduling/resources/{chair}/availability",
        json=[{"weekday": d, "start_time": "09:00", "end_time": "17:00"} for d in range(1, 6)],
    )
    return shop, chair


def test_dental_chair_hold_expiry_reschedule_and_cancel(dental, freeze_time):
    s, chair = dental
    at = "2026-11-12T17:00:00Z"  # both providers work Thursday
    response = s.owner.post(
        "/api/v1/scheduling/holds",
        json={"service_id": s.brake, "resource_id": s.jordan, "customer_id": s.ben, "starts_at": at},
    )
    assert response.status_code == 201, response.text
    hold = response.json()
    assert s.book(s.brake, s.ada, at, s.sky).status_code == 409
    freeze_time(z(hold["hold_expires_at"]) + timedelta(seconds=1))
    booked = s.book(s.brake, s.ada, at, s.sky)
    assert booked.status_code == 201, booked.text
    a = booked.json()
    assert {r["resource_id"] for r in a["resources"]} == {s.sky, chair}
    moved = s.owner.post(
        f"/api/v1/scheduling/appointments/{a['id']}/reschedule",
        json={"version": a["version"], "starts_at": "2026-11-12T20:00:00Z"},
    )
    assert moved.status_code == 200, moved.text
    assert s.book(s.brake, s.ben, at).status_code == 201
    response = s.owner.post(
        f"/api/v1/scheduling/appointments/{a['id']}/cancel",
        json={"version": moved.json()["version"], "reason": "Synthetic cancellation"},
    )
    assert response.status_code == 200, response.text
    assert s.book(s.brake, s.ada, "2026-11-12T20:00:00Z").status_code == 201
    with transaction(s.ctx()) as db:
        exported = lifecycle.export(db, s.ctx())
        assert list(exported["appointment_resources"])


def test_dental_slots_require_chair_for_full_buffer(dental):
    s, chair = dental
    assert s.book(s.brake, s.ben, "2026-11-12T17:00:00Z").status_code == 201
    slots = s.slots(s.brake, "2026-11-12T17:00:00Z", "2026-11-12T20:00:00Z", resource_id=s.sky)
    assert slots[0]["starts_at"] == "2026-11-12T18:15:00Z"
    assert slots[0]["resources"][1]["resource_id"] == chair


def test_dental_concurrent_providers_same_chair(dental):
    s, chair = dental
    barrier = threading.Barrier(2)
    results = []

    def book(provider, customer):
        try:
            with transaction(s.ctx()) as db:
                barrier.wait(timeout=10)
                service.book(
                    db,
                    s.ctx(),
                    BookingRequest(
                        customer_id=customer,
                        service_id=s.brake,
                        resource_id=provider,
                        starts_at=z("2026-11-12T17:00:00Z"),
                    ),
                )
            results.append("booked")
        except Conflict:
            results.append("conflict")

    threads = [threading.Thread(target=book, args=(r, c)) for r, c in [(s.jordan, s.ben), (s.sky, s.ada)]]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(results) == ["booked", "conflict"]
    with transaction(s.ctx()) as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(AppointmentResource).where(
                            AppointmentResource.resource_id == uuid.UUID(chair), AppointmentResource.active
                        )
                    )
                )
            )
            == 1
        )


def test_database_constraint_rejects_chair_race_even_without_slot_search(dental):
    """Bypass the finder: two different primaries still collide in the secondary exclusion constraint."""
    s, chair = dental
    from app.modules.scheduling.models import Appointment
    from app.modules.scheduling.schemas import AllocatedResource, SlotOut

    barrier = threading.Barrier(2)
    results = []

    def insert(provider, customer):
        at = z("2026-11-12T17:00:00Z")
        try:
            with transaction(s.ctx()) as db:
                row = Appointment(
                    id=uuid.uuid4(),
                    business_id=s.business,
                    customer_id=customer,
                    service_id=s.brake,
                    resource_id=provider,
                    location_id=service._resource(db, s.ctx(), uuid.UUID(provider)).location_id,
                    starts_at=at,
                    ends_at=at + timedelta(hours=1),
                    blocked_until=at + timedelta(minutes=75),
                    timezone="America/Denver",
                    status="held",
                    hold_expires_at=at,
                    source="staff",
                )
                slot = SlotOut(
                    starts_at=at,
                    ends_at=at + timedelta(hours=1),
                    resource_id=provider,
                    resource_name="Provider",
                    local_label="Synthetic",
                    resources=[AllocatedResource(role="chair", resource_id=chair, resource_name="Chair 1")],
                )
                barrier.wait(timeout=10)
                service._insert_guarded(db, row, slot)
            results.append("held")
        except Conflict:
            results.append("conflict")

    threads = [threading.Thread(target=insert, args=(r, c)) for r, c in [(s.jordan, s.ben), (s.sky, s.ada)]]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert sorted(results) == ["conflict", "held"]


def test_dental_alternative_chair_provider_busy_and_location(dental):
    s, _chair = dental
    from app.modules.org import service as org_service
    from app.modules.org.schemas import LocationCreate
    from app.modules.scheduling.schemas import ResourceIn, RuleIn

    with transaction(s.ctx()) as db:
        other = org_service.create_location(db, s.ctx(), LocationCreate(name="Other clinic", timezone="America/Denver"))
        remote = service.create_resource(
            db, s.ctx(), ResourceIn(name="Remote chair", kind="room", location_id=other.id)
        )
        service.replace_rules(db, s.ctx(), remote.id, [RuleIn(weekday=3, start_time="09:00", end_time="17:00")])
    assert s.book(s.brake, s.ben, "2026-11-12T17:00:00Z").status_code == 201
    assert s.slots(s.brake, "2026-11-12T17:00:00Z", "2026-11-12T17:15:00Z", resource_id=s.sky) == []
    with transaction(s.ctx()) as db:
        extra = service.create_resource(db, s.ctx(), ResourceIn(name="Chair 2", kind="room"))
        service.replace_rules(db, s.ctx(), extra.id, [RuleIn(weekday=3, start_time="09:00", end_time="17:00")])
    slots = s.slots(s.brake, "2026-11-12T17:00:00Z", "2026-11-12T17:15:00Z", resource_id=s.sky)
    assert slots[0]["resources"][1]["resource_id"] == str(extra.id)
    assert not s.slots(s.brake, "2026-11-12T17:00:00Z", "2026-11-12T17:15:00Z", resource_id=s.jordan)


def test_dental_expiry_job_and_completion_release_all_resources(dental, freeze_time):
    s, _chair = dental
    response = s.owner.post(
        "/api/v1/scheduling/holds",
        json={
            "customer_id": s.ben,
            "service_id": s.brake,
            "resource_id": s.jordan,
            "starts_at": "2026-11-12T17:00:00Z",
        },
    )
    assert response.status_code == 201, response.text
    freeze_time(z(response.json()["hold_expires_at"]) + timedelta(seconds=1))
    with transaction(s.ctx()) as db:
        service.job_release_expired_holds(db, s.ctx(), {})
        assert not list(db.scalars(select(AppointmentResource).where(AppointmentResource.active)))
    booked = s.book(s.brake, s.ben, "2026-11-12T17:00:00Z").json()
    response = s.owner.post(
        f"/api/v1/scheduling/appointments/{booked['id']}/complete", json={"version": booked["version"]}
    )
    assert response.status_code == 200, response.text
    assert s.book(s.brake, s.ada, "2026-11-12T17:00:00Z", s.sky).status_code == 201


def test_migration_has_no_backfill_and_protects_legacy_primary(dental, owner_engine):
    s, chair = dental
    from alembic import command
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.modules.scheduling.models import Appointment
    from tests.architecture.test_migrations import _cfg

    room_service = s.owner.post(
        "/api/v1/catalog/items",
        json={"name": "Room booking", "kind": "service", "resource_kind": "room", "duration_minutes": 60},
    ).json()["id"]
    assert s.book(room_service, s.ben, "2026-11-12T17:00:00Z", chair).status_code == 201
    cfg = _cfg()
    try:
        command.downgrade(cfg, "20261106_1200_customer_care")
        command.upgrade(cfg, "head")
        with owner_engine.connect() as conn:
            assert conn.scalar(text("select count(*) from appointment_resources")) == 0
        with transaction(s.ctx()) as db:
            at = z("2026-11-12T17:00:00Z")
            row = Appointment(
                id=uuid.uuid4(),
                business_id=s.business,
                customer_id=s.ada,
                service_id=s.brake,
                resource_id=s.jordan,
                location_id=service._resource(db, s.ctx(), uuid.UUID(s.jordan)).location_id,
                starts_at=at,
                ends_at=at + timedelta(hours=1),
                blocked_until=at + timedelta(minutes=75),
                timezone="America/Denver",
                status="confirmed",
                source="staff",
            )
            service._insert_guarded(db, row)
            with pytest.raises(IntegrityError):
                with db.begin_nested():
                    db.add(
                        AppointmentResource(
                            business_id=s.business,
                            appointment_id=row.id,
                            resource_id=chair,
                            role="chair",
                            starts_at=at,
                            blocked_until=at + timedelta(minutes=75),
                            active=True,
                        )
                    )
                    db.flush()
    finally:
        command.upgrade(cfg, "head")
