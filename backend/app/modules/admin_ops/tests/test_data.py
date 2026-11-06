"""M18: data export and customer erasure (docs/roadmap/modules/M18-admin-ops.md#tests)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.identity.testing import login_as
from app.platform import clock, events, jobs
from app.platform.ids import fixture_id
from app.platform.registry import Registry
from app.seed import seed

NOW = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)
CYCLE, GARDEN = fixture_id("biz-cycle"), fixture_id("biz-garden")


@pytest.fixture
def demo(registry: Registry) -> Any:
    clock.set_clock(lambda: NOW)
    seed()
    app = create_app(registry)
    return {"app": app, "owner": login_as(app, CYCLE, ["owner"]), "registry": registry}


def customer(owner: TestClient, name: str) -> dict[str, Any]:
    found = owner.get("/api/v1/crm/customers", params={"q": name}).json()["items"]
    return dict(next(c for c in found if c["display_name"] == name))


def export_zip(client: TestClient) -> zipfile.ZipFile:
    r = client.post("/api/v1/admin/exports")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip" and "attachment" in r.headers["content-disposition"]
    return zipfile.ZipFile(io.BytesIO(r.content))


def test_export_contains_every_module_of_one_business_and_nothing_of_another(demo: Any) -> None:
    archive = export_zip(demo["owner"])
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["business_id"] == str(CYCLE)
    expected = {
        "org",
        "identity",
        "audit",
        "approvals",
        "notifications",
        "crm",
        "catalog",
        "inventory",
        "conversations",
        "knowledge",
        "scheduling",
        "marketing",
        "sales",
        "purchasing",
        "ai",
        "admin_ops",
    }
    assert set(manifest["modules"]) == expected
    assert set(manifest["modules"]["admin_ops"]) == {"erasure_requests", "jobs", "domain_events", "event_deliveries"}
    assert set(manifest["modules"]["ai"]) == {
        "ai_runs", "ai_run_steps", "ai_effects", "ai_usage_daily", "assistant_threads", "assistant_messages",
    }  # fmt: skip
    # Nothing is sent while the demo data is loaded, and the demo data holds no assistant runs.
    for module in expected - {"notifications", "admin_ops", "ai"}:
        assert any(count > 0 for count in manifest["modules"][module].values()), f"{module} exported no rows"
    assert set(manifest["modules"]["notifications"]) == {"outbox_messages", "delivery_attempts", "in_app_notifications"}
    everything = "".join(archive.read(name).decode() for name in archive.namelist())
    assert "Ada Example" in everything and "Brake pads (pair)" in everything and "Winter tune-up offer" in everything
    assert "Gwen Example" not in everything and "Example Garden Store" not in everything  # the other business
    for name in archive.namelist():
        if name == "manifest.json":
            continue
        for row in json.loads(archive.read(name)):
            if "business_id" in row:
                assert row["business_id"] == str(CYCLE), name
    assert "token_hash" not in everything and "oidc_subject" not in everything  # credentials are not data


LEAK_COLUMNS = (
    ("audit_events", "summary"),
    ("audit_events", "changes::text"),
    ("action_proposals", "title"),
    ("proposal_revisions", "payload::text"),
    ("proposal_revisions", "summary"),
    ("proposal_revisions", "preview::text"),
    ("approval_decisions", "coalesce(comment, '')"),
    ("in_app_notifications", "title || body"),
    ("domain_events", "payload::text"),
    ("jobs", "payload::text"),
    ("idempotency_keys", "response_body::text"),
    ("outbox_messages", "coalesce(recipient, '') || variables::text"),
    ("messages", "body"),
    ("conversations", "concat(subject, visitor_name, visitor_email, visitor_phone)"),
    ("stock_movements", "coalesce(note, '')"),
    ("tags", "name"),
    ("campaigns", "coalesce(purpose, '')"),
    ("campaign_revisions", "subject || body_markdown"),
    ("appointments", "concat(notes, cancelled_reason)"),
)


def test_only_the_owner_may_export_or_erase(demo: Any) -> None:
    admin = login_as(demo["app"], CYCLE, ["admin"])
    assert admin.post("/api/v1/admin/exports").status_code == 403
    ada = customer(demo["owner"], "Ada Example")
    assert (
        admin.post("/api/v1/admin/erasures", json={"customer_id": ada["id"], "confirm_name": "Ada Example"}).status_code
        == 403
    )
    assert login_as(demo["app"], CYCLE, ["auditor"]).get("/api/v1/admin/erasures").status_code == 403


def test_erasure_removes_personal_data_across_modules_and_keeps_ids(demo: Any, owner_engine: Engine) -> None:
    owner, app = demo["owner"], demo["app"]
    ada = customer(owner, "Ada Example")
    detail = owner.get(f"/api/v1/crm/customers/{ada['id']}").json()
    owner.post(
        f"/api/v1/crm/customers/{ada['id']}/notes",
        json={"body": "Prefers Tuesday mornings, rides a green Trek", "pinned": False},
    )
    ben = customer(owner, "Ben Example")

    # An approval request that names Ada is still open: erasing her now would corrupt what the approver is looking at.
    proposal = login_as(app, CYCLE, ["operator"]).post(
        "/api/v1/approvals/proposals",
        json={
            "action_type": "crm.update_customer",
            "title": "Rename Ada Example",
            "payload": {
                "customer_id": ada["id"],
                "customer_version": detail["version"],
                "changes": {"given_name": "Adaline"},
            },
        },
    )
    assert proposal.status_code == 201, proposal.text
    blocked = owner.post("/api/v1/admin/erasures", json={"customer_id": ada["id"], "confirm_name": "Ada Example"})
    assert blocked.status_code == 409 and blocked.json()["code"] == "admin_ops.erasure_blocked"
    assert "open approval request" in blocked.json()["reasons"][0]
    events.deliver_pending(demo["registry"])  # the approver's in-app notification copies the proposal title
    rev = proposal.json()["current_revision"]
    owner.post(
        f"/api/v1/approvals/proposals/{proposal.json()['id']}/decisions",
        json={"decision": "reject", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )

    # A typed confirmation is required.
    wrong = owner.post("/api/v1/admin/erasures", json={"customer_id": ada["id"], "confirm_name": "ada"})
    assert wrong.status_code == 422 and wrong.json()["code"] == "admin_ops.confirmation_mismatch"

    done = owner.post("/api/v1/admin/erasures", json={"customer_id": ada["id"], "confirm_name": "Ada Example"})
    assert done.status_code == 200, done.text
    report = done.json()["report"]
    assert report["crm"]["contact_points_blanked"] == 2 and report["crm"]["notes_deleted"] == 1
    assert report["conversations"]["messages_erased"] >= 1 and report["audit"]["audit_events_redacted"] >= 1
    assert report["approvals"]["approval_records_redacted"] >= 1

    gone = owner.get(f"/api/v1/crm/customers/{ada['id']}")
    assert gone.status_code == 404  # an erased customer is not shown any more
    with owner_engine.connect() as conn:
        row = conn.execute(
            text("select display_name, given_name, status, source from customers where id = :i"), {"i": ada["id"]}
        ).one()
        assert tuple(row) == ("Erased customer", None, "erased", "staff") and ada["id"]  # the row and its id remain
        contacts = conn.execute(
            text("select value, is_active from contact_points where customer_id = :i"), {"i": ada["id"]}
        ).all()
        assert {(v, a) for v, a in contacts} == {("[erased]", False)}
        consent = conn.execute(
            text("select status, evidence from consent_records where customer_id = :i"), {"i": ada["id"]}
        ).all()
        assert (
            consent and all(e == "[erased]" for _, e in consent) and "granted" in {s for s, _ in consent}
        )  # decisions are kept
        assert (
            conn.execute(text("select count(*) from customer_notes where customer_id = :i"), {"i": ada["id"]}).scalar()
            == 0
        )
        assert (
            conn.execute(
                text("select count(*) from timeline_entries where customer_id = :i"), {"i": ada["id"]}
            ).scalar()
            == 0
        )
        bodies = (
            conn.execute(
                text(
                    "select m.body from messages m join conversations c on c.id = m.conversation_id "
                    "where c.business_id = :b and c.channel = 'web_chat'"
                    " and c.visitor_email is null and c.visitor_name is null"
                ),
                {"b": str(CYCLE)},
            )
            .scalars()
            .all()
        )
        assert bodies and set(bodies) == {"[erased]"}
        # No text anywhere in the business still names her, including the rejected rename to "Adaline" (a value that
        # is not her current name, found through the proposal's link to her id).
        for table, column in LEAK_COLUMNS:
            for name in ("Ada Example", "Adaline"):
                leaked = conn.execute(
                    text(f"select count(*) from {table} where business_id = :b and ({column}) ilike :n"),  # noqa: S608
                    {"b": str(CYCLE), "n": f"%{name}%"},
                ).scalar()
                assert leaked == 0, f"{table}.{column} still contains {name}"
        assert (
            conn.execute(
                text(
                    "select count(*) from audit_events where business_id = :b"
                    " and action = 'crm.customer.erased' and target_id = :i"
                ),
                {"b": str(CYCLE), "i": ada["id"]},
            ).scalar()
            == 1
        )
        assert (
            conn.execute(
                text("select count(*) from erasure_requests where business_id = :b"), {"b": str(CYCLE)}
            ).scalar()
            == 1
        )

    # Idempotent: a second request returns the earlier result and changes nothing.
    again = owner.post("/api/v1/admin/erasures", json={"customer_id": ada["id"], "confirm_name": "anything"})
    assert again.status_code == 200 and again.json()["id"] == done.json()["id"]
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from erasure_requests")).scalar() == 1
    # Other customers and the other business are untouched.
    assert customer(owner, "Ben Example")["display_name"] == ben["display_name"]
    garden = login_as(app, GARDEN, ["owner"])
    assert customer(garden, "Gwen Example")["display_name"] == "Gwen Example"
    assert [e["customer_id"] for e in owner.get("/api/v1/admin/erasures").json()] == [ada["id"]]


def test_upcoming_appointment_blocks_erasure_until_it_is_cancelled(demo: Any, owner_engine: Engine) -> None:
    owner = demo["owner"]
    ben = customer(owner, "Ben Example")
    blocked = owner.post("/api/v1/admin/erasures", json={"customer_id": ben["id"], "confirm_name": "Ben Example"})
    assert blocked.status_code == 409 and "upcoming appointment" in blocked.json()["reasons"][0]
    appointments = owner.get(
        "/api/v1/scheduling/appointments",
        params={"start": "2026-11-09T00:00:00Z", "end": "2026-11-12T00:00:00Z", "customer_id": ben["id"]},
    ).json()
    appointment = appointments[0]
    with owner_engine.begin() as conn:
        conn.execute(
            text("update appointments set notes = 'Ben says the chain skips' where id = :i"), {"i": appointment["id"]}
        )
    owner.post(
        f"/api/v1/scheduling/appointments/{appointment['id']}/cancel",
        json={"version": appointment["version"], "reason": "Ben is in hospital, call his wife"},
    )
    done = owner.post("/api/v1/admin/erasures", json={"customer_id": ben["id"], "confirm_name": "Ben Example"})
    assert done.status_code == 200 and done.json()["report"]["scheduling"]["appointment_texts_removed"] >= 1
    with owner_engine.connect() as conn:
        row = conn.execute(
            text("select notes, cancelled_reason, starts_at from appointments where id = :i"), {"i": appointment["id"]}
        ).one()
        assert row[0] is None and row[1] is None and row[2] is not None  # the appointment itself stays


def test_sent_campaign_messages_to_the_person_lose_address_and_text(demo: Any, owner_engine: Engine) -> None:
    owner, app = demo["owner"], demo["app"]
    marketer = login_as(app, CYCLE, ["marketing_manager"])
    campaign = marketer.get("/api/v1/marketing/campaigns").json()[0]
    submitted = marketer.post(f"/api/v1/marketing/campaigns/{campaign['id']}/submit").json()
    p = owner.get(f"/api/v1/approvals/proposals/{submitted['proposal_id']}").json()["current_revision"]
    admin = login_as(app, CYCLE, ["admin"])
    assert (
        admin.post(
            f"/api/v1/approvals/proposals/{submitted['proposal_id']}/decisions",
            json={"decision": "approve", "revision_id": p["id"], "payload_hash": p["payload_hash"]},
        ).status_code
        == 200
    )
    for _ in range(3):
        jobs.run_pending(demo["registry"])
        events.deliver_pending(demo["registry"])
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("select count(*) from outbox_messages where recipient = 'ada@example.com'")).scalar() == 1
        )
    ada = customer(owner, "Ada Example")
    assert (
        owner.post("/api/v1/admin/erasures", json={"customer_id": ada["id"], "confirm_name": "Ada Example"}).status_code
        == 200
    )
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "select recipient, variables::text, status from outbox_messages"
                " where related_type = 'campaign' and recipient = '[erased]'"
            )
        ).all()
        assert len(rows) == 1 and rows[0][1] == "{}" and rows[0][2] in ("delivered", "pending", "sending")
        assert (
            conn.execute(text("select count(*) from outbox_messages where recipient = 'ada@example.com'")).scalar() == 0
        )


def test_redaction_functions_refuse_another_business(demo: Any, owner_engine: Engine) -> None:
    """The security-definer functions only act for the business set in the session."""
    from sqlalchemy.exc import DBAPIError

    from app.platform.context import Context
    from app.platform.db import transaction

    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=CYCLE, permissions=frozenset())
    calls = (
        "erase_redact_audit(:b, array['Gwen Example'], array[]::uuid[])",
        "erase_redact_proposals(:b, array['Gwen Example'], array[]::uuid[])",
        "erase_redact_stock_notes(:b, array['Gwen Example'], array[]::uuid[])",
        "erase_redact_campaign_revisions(:b, array['Gwen Example'])",
        "erase_redact_consent(:b, gen_random_uuid())",
    )
    for call in calls:
        with transaction(ctx) as db:
            assert db.execute(text(f"select {call}"), {"b": str(CYCLE)}).scalar() is not None
        with pytest.raises(DBAPIError), transaction(ctx) as db:
            db.execute(text(f"select {call}"), {"b": str(GARDEN)})


def test_definer_functions_search_the_temporary_schema_last(owner_engine: Engine) -> None:
    """Otherwise the application role could shadow a table with a temporary one inside an owner-rights function."""
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text("select proname, proconfig from pg_proc where prosecdef and pronamespace = 'public'::regnamespace")
        ).all()
    assert rows
    for name, config in rows:
        assert config and "search_path=pg_catalog, public, pg_temp" in config, name


def test_json_redaction_keeps_numbers_keys_and_ids(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        redacted = conn.execute(
            text("""select redact_jsonb('{"100": 100, "name": "Shop 100", "list": ["100", 7]}'::jsonb, array['100'])""")
        ).scalar()
        scrubbed = conn.execute(
            text(
                'select scrub_jsonb(\'{"id": "01a17d0e-ed10-7f0e-a569-6ccb2258fbaf", "at": "2026-11-10T16:00:00Z",'
                ' "n": 3, "note": "call his wife"}\'::jsonb)'
            )
        ).scalar()
    assert redacted == {"100": 100, "name": "Shop [erased]", "list": ["[erased]", 7]}
    assert scrubbed == {
        "id": "01a17d0e-ed10-7f0e-a569-6ccb2258fbaf", "at": "2026-11-10T16:00:00Z", "n": 3, "note": "[erased]",
    }  # fmt: skip


def test_erasure_reaches_archived_contacts_phone_visitors_replies_notes_tags_and_caches(
    demo: Any, owner_engine: Engine
) -> None:
    owner = demo["owner"]
    created = owner.post(
        "/api/v1/crm/customers",
        headers={"Idempotency-Key": "create-zed-0001"},  # the cached response repeats the personal data
        json={
            "display_name": "Zed Example", "given_name": "Zedediah", "family_name": "Example",
            "contact_points": [
                {"type": "email", "value": "zed@example.com", "is_primary": True},
                {"type": "phone", "value": "+1 303 555 0199"},
            ],
        },
    )  # fmt: skip
    assert created.status_code == 201, created.text
    zed = created.json()
    assert (
        owner.put(f"/api/v1/crm/customers/{zed['id']}/tags", json={"tags": ["Zedediah VIP", "regular"]}).status_code
        == 200
    )
    # A phone-only visitor (written differently) whose conversation was never linked to the customer.
    started = TestClient(demo["app"], base_url="https://testserver").post(
        "/api/v1/public/example-cycle-works/chat",
        json={"name": "Zedediah", "phone": "(303) 555-0199", "message": "Hi, it's Zedediah, is my bike ready?"},
    )
    assert started.status_code == 201, started.text
    with owner_engine.connect() as conn:
        conv_id = str(
            conn.execute(text("select id from conversations where visitor_phone = '(303) 555-0199'")).scalar()
        )
    reply = owner.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"direction": "outbound", "body": "Yes Zedediah, come by at 5"},
    )
    assert reply.status_code == 201, reply.text
    with owner_engine.begin() as conn:
        pads = conn.execute(
            text("select id from catalog_items where business_id = :b and name = 'Brake pads (pair)'"),
            {"b": str(CYCLE)},
        ).scalar()
        conn.execute(
            text("update campaigns set purpose = 'Win back Zed Example' where business_id = :b"), {"b": str(CYCLE)}
        )
    moved = owner.post(
        "/api/v1/inventory/movements",
        json={"item_id": str(pads), "quantity": "1", "reason": "receipt", "note": "Returned by Zed Example"},
    )
    assert moved.status_code == 201, moved.text
    assert owner.post(f"/api/v1/crm/customers/{zed['id']}/archive", json={"version": zed["version"]}).status_code == 200

    done = owner.post("/api/v1/admin/erasures", json={"customer_id": zed["id"], "confirm_name": "Zed Example"})
    assert done.status_code == 200, done.text
    with owner_engine.connect() as conn:
        for table, column in LEAK_COLUMNS:
            for value in ("Zed Example", "Zedediah", "zed@example.com", "555 0199", "555-0199", "5550199"):
                leaked = conn.execute(
                    text(f"select count(*) from {table} where business_id = :b and ({column}) ilike :n"),  # noqa: S608
                    {"b": str(CYCLE), "n": f"%{value}%"},
                ).scalar()
                assert leaked == 0, f"{table}.{column} still contains {value}"
        suppression = conn.execute(
            text("select reason from suppressions where business_id = :b and value_normalized = 'zed@example.com'"),
            {"b": str(CYCLE)},
        ).scalar()
        assert suppression == "erased"  # never mailed again, even if the address is imported later
        assert conn.execute(text("select count(*) from tags where name = 'regular'")).scalar() >= 1  # shared tag kept
        assert (
            conn.execute(
                text("select count(*) from stock_movements where business_id = :b and note = 'Returned by [erased]'"),
                {"b": str(CYCLE)},
            ).scalar()
            == 1
        )  # quantity and the movement stay


def test_export_leaves_out_signed_unsubscribe_links(demo: Any, owner_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "insert into outbox_messages (id, business_id, channel, recipient, template_key, variables,"
                " idempotency_key, status, related_type)"
                " values (gen_random_uuid(), :b, 'simulated', 'ada@example.com', 'marketing_campaign',"
                ' \'{"subject": "Hi", "unsubscribe_url": "https://x.example.com/u/secret-token"}\','
                " 'export-test-1', 'delivered', 'campaign')"
            ),
            {"b": str(CYCLE)},
        )
    archive = export_zip(demo["owner"])
    everything = "".join(archive.read(n).decode() for n in archive.namelist())
    assert "secret-token" not in everything and '"subject": "Hi"' in everything


def test_two_concurrent_erasures_of_one_customer_record_one_result(demo: Any, owner_engine: Engine) -> None:
    import threading
    import uuid

    from app.modules.admin_ops import service
    from app.platform.context import Context
    from app.platform.db import transaction

    dana = customer(demo["owner"], "Dana Example")
    ctx = Context(
        request_id="t", actor_kind="user", user_id=None, business_id=CYCLE,
        permissions=frozenset({"admin_ops.data.manage"}),
    )  # fmt: skip
    barrier, results = threading.Barrier(2), []

    def attempt() -> None:
        barrier.wait()
        with transaction(ctx) as db:
            results.append(service.erase_customer(db, ctx, uuid.UUID(dana["id"]), "Dana Example").id)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 2 and results[0] == results[1]
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text("select count(*) from erasure_requests where customer_id = :i"), {"i": dana["id"]}
            ).scalar()
            == 1
        )
