"""P3-14: the synthetic dataset loads through the services and reproduces the scenario facts (data/README.md)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.identity.testing import login_as
from app.platform import clock, events, jobs
from app.platform.config import get_settings
from app.platform.ids import fixture_id
from app.seed import seed

pytestmark = pytest.mark.integration
CYCLE = fixture_id("biz-cycle")


def test_seed_reproduces_the_demo_story(registry) -> None:  # type: ignore[no-untyped-def]
    clock.set_clock(lambda: datetime(2026, 11, 5, 17, 30, tzinfo=UTC))
    counts = seed()
    assert counts["example-cycle-works"] == {
        "staff": 7,
        "kb_articles": 6,
        "catalog_items": 7,
        "customers": 6,
        "appointments": 3,
        "conversations": 3,
        "proposals": 1,
        "campaigns": 1,
        "suppliers": 4,
        "purchase_orders": 8,
        "invoices": 2,
        "quote_requests": 1,
    }
    app = create_app(registry)
    owner = login_as(app, CYCLE, ["owner"])

    levels = {x["item_name"]: x for x in owner.get("/api/v1/inventory/levels").json()}
    pads = levels["Brake pads (pair)"]
    assert (Decimal(pads["on_hand"]), Decimal(pads["reserved"]), Decimal(pads["available"])) == (3, 1, 2)
    assert levels["Cable set"]["below_reorder"] is True and pads["below_reorder"] is False

    brake = next(i for i in owner.get("/api/v1/catalog/items", params={"q": "Brake service"}).json()["items"])
    slots = owner.get(
        "/api/v1/scheduling/slots",
        params={"service_id": brake["id"], "earliest": "2026-11-10T16:00:00Z", "latest": "2026-11-10T19:00:00Z"},
    ).json()
    assert [(s["local_label"], s["resource_name"]) for s in slots] == [("Tue 10 Nov 11:45 MST", "Jordan (technician)")]

    pending = owner.get("/api/v1/approvals/proposals", params={"status": "pending_review"}).json()["items"]
    assert [p["action_type"] for p in pending] == ["inventory.adjust_stock"]
    inbox = owner.get("/api/v1/conversations").json()["items"]
    assert {c["channel"] for c in inbox} == {"web_chat", "phone_log"}

    from app.modules.crm import public as crm
    from app.modules.org.testing import system_ctx
    from app.platform.db import transaction

    with transaction(system_ctx(CYCLE)) as db:
        eligible = sorted(e.display_name for e in crm.list_marketing_eligible(db, system_ctx(CYCLE), "email"))
    assert eligible == ["Ada Example", "Example Courier Co."]

    public = TestClient(app).get("/api/v1/public/example-garden-store/profile").json()
    assert public["timezone"] == "Europe/London"
    with pytest.raises(SystemExit, match="already exists"):
        seed()
    again = seed(do_reset=True)
    assert again["example-garden-store"]["customers"] == 2
    assert isinstance(uuid.UUID(str(CYCLE)), uuid.UUID)


def test_reset_replaces_a_hand_bootstrapped_demo_business(registry, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    from app.modules.org.testing import make_business

    by_hand = make_business("example-cycle-works")  # e.g. created with `bootstrap-business` before seeding
    assert by_hand != CYCLE
    with owner_engine.begin() as conn:  # a visitor hit the public chat limit earlier
        conn.execute(
            text("insert into rate_limits (key, window_start, count) values (:k, now(), 5)"),
            {"k": f"chat-start:{by_hand}:203.0.113.7"},
        )
    with pytest.raises(SystemExit):
        seed()  # refuses to seed over existing data without --reset
    seed(do_reset=True)
    with owner_engine.connect() as conn:
        ids = conn.execute(text("select id from businesses where slug = 'example-cycle-works'")).scalars().all()
        limits = conn.execute(text("select count(*) from rate_limits")).scalar()
    assert ids == [CYCLE] and limits == 0


def test_seeded_winter_campaign_matches_the_documented_facts(registry) -> None:  # type: ignore[no-untyped-def]
    """data/README.md: eligible are Ada and Example Courier Co.; Ben denied, Dana never asked, Dan withdrew."""
    clock.set_clock(lambda: datetime(2026, 11, 5, 17, 30, tzinfo=UTC))
    seed()
    marketer = login_as(create_app(registry), CYCLE, ["marketing_manager"])
    campaign = marketer.get("/api/v1/marketing/campaigns").json()[0]
    assert campaign["name"] == "Winter tune-up offer" and campaign["status"] == "draft"
    preview = marketer.get(f"/api/v1/marketing/segments/{campaign['segment_id']}/preview").json()
    assert sorted(m["display_name"] for m in preview["members"]) == ["Ada Example", "Example Courier Co."]
    assert preview["matched"] == 5
    assert preview["excluded_counts"]["no_consent"] == 3  # Ben (denied), Dana (never asked), Dan (withdrew)
    suppressed = [s["value_normalized"] for s in marketer.get("/api/v1/marketing/suppressions").json()]
    assert suppressed == ["dan.example@example.com"]
    # The facts hold when the campaign is submitted: the frozen audience is exactly the eligible people.
    submitted = marketer.post(f"/api/v1/marketing/campaigns/{campaign['id']}/submit").json()
    assert submitted["status"] == "in_review" and submitted["snapshot"]["eligible_count"] == 2


def test_reset_demo_command_needs_confirmation_and_recreates_the_demo(registry, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    from app.cli import main

    with pytest.raises(SystemExit):
        main(["reset-demo"])  # refuses without --yes
    seed()
    with owner_engine.begin() as conn:
        conn.execute(text("update customers set display_name = 'Vandalised' where display_name = 'Ada Example'"))
    main(["reset-demo", "--yes"])
    with owner_engine.connect() as conn:
        names = (
            conn.execute(text("select display_name from customers where display_name in ('Ada Example', 'Vandalised')"))
            .scalars()
            .all()
        )
    assert names == ["Ada Example"]


# ------------------------------------------------------------------ procurement and payment showcase (data/README.md)
BASE = "/api/v1/purchasing"
PARTS, DIRECT, WHOLESALE = "Example Parts Supply", "Example Brake Direct", "Example Cycle Wholesale"
THURSDAY = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)


def _owner(registry: Any) -> TestClient:
    return login_as(create_app(registry), CYCLE, ["owner"])


def _pads(owner: TestClient) -> dict[str, Any]:
    items = owner.get("/api/v1/catalog/items", params={"q": "Brake pads"}).json()["items"]
    return dict(next(i for i in items if i["name"] == "Brake pads (pair)"))


def _open_request(owner: TestClient) -> dict[str, Any]:
    requests = owner.get(f"{BASE}/quote-requests", params={"status": "sent"}).json()["items"]
    assert len(requests) == 1
    return dict(requests[0])


def _quote_link(owner_engine: Engine, supplier_email: str) -> str:
    """The supplier's public-page path, read from the outbox like the e-mail would carry it (HTTP hides it)."""
    with owner_engine.connect() as conn:
        url = conn.execute(
            text(
                "select variables->>'quote_url' from outbox_messages"
                " where template_key = 'quote_request' and recipient = :recipient"
            ),
            {"recipient": supplier_email},
        ).scalar_one()
    return "/api/v1/public/quotes/" + str(url).rsplit("/", 1)[1]


def test_three_suppliers_sell_brake_pads_with_different_price_lead_time_and_history(registry) -> None:  # type: ignore[no-untyped-def]
    clock.set_clock(lambda: THURSDAY)
    seed()
    owner = _owner(registry)
    options = {o["supplier_name"]: o for o in owner.get(f"{BASE}/items/{_pads(owner)['id']}/suppliers").json()}
    assert set(options) == {PARTS, DIRECT, WHOLESALE}  # the existing supplier is kept
    assert all(o["has_email"] and o["currency"] == "USD" for o in options.values())
    assert [options[n]["last_price_minor"] for n in (WHOLESALE, PARTS, DIRECT)] == [1050, 1200, 1400]
    assert [options[n]["lead_time_days"] for n in (DIRECT, PARTS, WHOLESALE)] == [2, 3, 16]
    history = {n: (options[n]["orders_count"], options[n]["on_time_rate"]) for n in options}
    assert history == {DIRECT: (3, 1.0), PARTS: (0, None), WHOLESALE: (3, pytest.approx(1 / 3))}
    suppliers = owner.get(f"{BASE}/suppliers").json()["items"]
    assert {s["name"] for s in suppliers} == {PARTS, DIRECT, WHOLESALE, "Example Tyre Co."}
    assert all(s["email"].endswith("@example.com") for s in suppliers)


def test_open_quote_request_has_answers_and_a_pending_supplier_and_the_cheapest_does_not_win(registry) -> None:  # type: ignore[no-untyped-def]
    clock.set_clock(lambda: THURSDAY)
    seed()
    owner = _owner(registry)
    request = _open_request(owner)
    assert [(line["name"], line["quantity"]) for line in request["lines"]] == [("Brake pads (pair)", "10.000")]
    assert (request["reply_by"], request["needed_by"]) == ("2026-11-12", "2026-11-16")  # 7 and 11 days from the 5th
    invites = {i["supplier_name"]: i["status"] for i in request["suppliers"]}
    assert invites == {PARTS: "invited", DIRECT: "responded", WHOLESALE: "responded"}
    assert [q["source"] for q in request["quotes"]] == ["form", "form"]

    comparison = owner.get(f"{BASE}/quote-requests/{request['id']}/comparison").json()
    assert comparison["ineligible"] == []
    direct, wholesale = comparison["ranked"]  # the dearest and fastest wins; the cheapest is last
    assert (direct["supplier_name"], wholesale["supplier_name"]) == (DIRECT, WHOLESALE)
    assert wholesale["total_minor"] < direct["total_minor"] and wholesale["score"] < direct["score"]
    assert (direct["total_minor"], direct["days_late"], direct["reliability"], direct["score"]) == (14000, 0, 1.0, 0.85)
    assert (wholesale["total_minor"], wholesale["days_late"], wholesale["price_score"]) == (10500, 5, 1.0)
    assert wholesale["lead_score"] == pytest.approx(9 / 14) and wholesale["score"] == 0.811


def test_supplier_answers_on_the_public_page_and_the_ranking_changes(registry, owner_engine) -> None:  # type: ignore[no-untyped-def]
    clock.set_clock(lambda: THURSDAY)
    seed()
    owner = _owner(registry)
    request = _open_request(owner)
    page = TestClient(owner.app, base_url="https://testserver")  # the supplier is not logged in
    link = _quote_link(owner_engine, "parts@example.com")
    assert page.get(link).json()["lines"][0]["name"] == "Brake pads (pair)"
    answered = page.post(
        link, json={"lines": [{"item_id": _pads(owner)["id"], "unit_price_minor": 1180}], "lead_time_days": 3}
    )
    assert answered.json() == {"status": "responded"}

    ranked = owner.get(f"{BASE}/quote-requests/{request['id']}/comparison").json()["ranked"]
    assert [(q["supplier_name"], q["score"]) for q in ranked] == [(PARTS, 0.904), (DIRECT, 0.85), (WHOLESALE, 0.811)]
    assert (ranked[0]["reliability"], ranked[0]["orders_count"]) == (0.8, 0)  # no completed order: the default
    assert {i["status"] for i in owner.get(f"{BASE}/quote-requests/{request['id']}").json()["suppliers"]} == {
        "responded"
    }
    proposal = owner.post(
        f"{BASE}/purchase-orders/from-quote",
        json={"quote_id": ranked[0]["quote_id"], "quote_version": ranked[0]["quote_version"]},
    )
    assert proposal.status_code == 201 and proposal.json()["status"] == "pending_review"


def test_quote_request_emails_are_simulated_and_never_count_as_delivery_issues(
    registry: Any, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without SMTP the normal rule (notifications.email_channel) makes them simulated."""
    monkeypatch.setattr(get_settings(), "smtp_url", "")
    clock.set_clock(lambda: THURSDAY)
    seed()
    owner = _owner(registry)
    with owner_engine.connect() as conn:
        queued = conn.execute(text("select channel, recipient, status from outbox_messages")).all()
    assert sorted(tuple(r) for r in queued) == [
        ("simulated", "brakes@example.com", "pending"),
        ("simulated", "parts@example.com", "pending"),
        ("simulated", "wholesale@example.com", "pending"),
    ]
    for _ in range(3):  # what the worker does after the seed
        jobs.run_pending(registry)
        events.deliver_pending(registry)
    with owner_engine.connect() as conn:
        assert set(conn.execute(text("select status from outbox_messages")).scalars()) == {"delivered"}
    assert owner.get("/api/v1/dashboard/today").json()["delivery_issues"] == 0


SMTP_URL = "smtp://mail.example.com:587?from=no-reply@example.com"


def test_seed_queues_only_simulated_email_even_when_smtp_is_configured(
    registry: Any, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The demo suppliers are example.com addresses: a real provider would bounce the mail. The seed therefore queues
    its messages `simulated` from the start (no row is rewritten), and the worker never reaches the SMTP adapter."""
    from app.modules.notifications import public as notifications_public
    from app.modules.notifications.adapters import ADAPTERS

    class NeverSend:
        def send(self, *_: Any) -> None:
            raise AssertionError("the seed must not send real e-mail")

    queued: list[str] = []
    real_enqueue = notifications_public.enqueue_message

    def spy(*args: Any, **kwargs: Any) -> Any:
        queued.append(kwargs["channel"])  # what the row is created with, not what it is changed to later
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(get_settings(), "smtp_url", SMTP_URL)
    monkeypatch.setattr(notifications_public, "enqueue_message", spy)
    monkeypatch.setitem(ADAPTERS, "email", NeverSend())
    clock.set_clock(lambda: THURSDAY)
    seed()
    assert queued == ["simulated"] * 3
    with owner_engine.connect() as conn:
        rows = conn.execute(text("select channel, recipient, status from outbox_messages order by recipient")).all()
    assert [tuple(r) for r in rows] == [
        ("simulated", "brakes@example.com", "pending"),
        ("simulated", "parts@example.com", "pending"),
        ("simulated", "wholesale@example.com", "pending"),
    ]
    for _ in range(3):  # what the worker does after the seed
        jobs.run_pending(registry)
        events.deliver_pending(registry)
    with owner_engine.connect() as conn:
        done = conn.execute(text("select status, provider_message_id from outbox_messages")).all()
    assert {r.status for r in done} == {"delivered"}
    assert all(r.provider_message_id.startswith("simulated-") for r in done)
    assert _owner(registry).get("/api/v1/dashboard/today").json()["delivery_issues"] == 0


def test_seed_leaves_the_email_rule_alone_afterwards_and_after_a_failure(
    registry: Any, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override covers the seeding only: it ends with the seed, also when the seed stops with an error."""
    import app.seed as seed_module
    from app.modules.notifications import public as notifications_public

    monkeypatch.setattr(get_settings(), "smtp_url", SMTP_URL)
    clock.set_clock(lambda: THURSDAY)
    assert notifications_public.email_channel() == "email"
    seed()
    assert notifications_public.email_channel() == "email"  # normal sending is unaffected once the seed is done

    inside: list[str] = []

    def failing(*_: Any) -> None:
        inside.append(notifications_public.email_channel())
        raise RuntimeError("seed stopped half-way")

    monkeypatch.setattr(seed_module, "seed_business", failing)
    with pytest.raises(RuntimeError, match="half-way"):
        seed(do_reset=True)
    assert inside == ["simulated"]
    assert notifications_public.email_channel() == "email"


def test_late_purchase_order_is_sent_and_listed_as_late(registry) -> None:  # type: ignore[no-untyped-def]
    clock.set_clock(lambda: THURSDAY)
    seed()
    owner = _owner(registry)
    late = owner.get(f"{BASE}/late-purchase-orders").json()  # the list behind the "late" filter and the Today card
    assert len(late) == 1
    assert (late[0]["supplier_name"], late[0]["expected_delivery_date"], late[0]["days_late"]) == (
        WHOLESALE,
        "2026-11-01",
        4,
    )
    assert [(line["name"], line["quantity"]) for line in late[0]["open_lines"]] == [("Cable set", "5.000")]
    order = owner.get(f"{BASE}/purchase-orders/{late[0]['id']}").json()
    assert order["status"] == "sent" and order["number"] == late[0]["number"]
    assert order["approved_date"] == "2026-10-22" and order["sent_at"].startswith(
        "2026-10-22"
    )  # sent before it was due
    orders = owner.get(f"{BASE}/purchase-orders", params={"limit": 50}).json()["items"]
    assert sorted(o["status"] for o in orders) == ["approved", *["received"] * 6, "sent"]  # eight orders in all
    approved = owner.get(f"{BASE}/purchase-orders", params={"status": "approved"}).json()["items"]
    assert [(o["number"], o["supplier_name"], o["expected_delivery_date"]) for o in approved] == [
        ("PO-2026-0001", PARTS, None)  # the original order is unchanged (and, without a date, never late)
    ]


def test_overdue_invoice_is_issued_unpaid_and_has_an_email(registry) -> None:  # type: ignore[no-untyped-def]
    from app.modules.sales import public as sales
    from app.platform.db import transaction
    from app.platform.testing.auth import ctx_for

    clock.set_clock(lambda: THURSDAY)
    seed()
    owner = _owner(registry)
    ctx = ctx_for(CYCLE, ["sales.document.read"])
    with transaction(ctx) as db:
        overdue = sales.list_overdue_invoices(db, ctx)
    assert len(overdue) == 1
    invoice = overdue[0]
    assert (invoice.bill_to_name, invoice.bill_to_email) == ("Example Courier Co.", "fleet@example.com")
    assert (invoice.due_date, invoice.days_overdue, invoice.total_minor, invoice.last_reminder_at) == (
        date(2026, 10, 26),
        10,
        19000,
        None,
    )
    assert [i["id"] for i in owner.get("/api/v1/sales/invoices", params={"overdue": "true"}).json()["items"]] == [
        str(invoice.id)
    ]
    detail = owner.get(f"/api/v1/sales/invoices/{invoice.id}").json()
    assert (detail["status"], detail["kind"], detail["paid_at"], detail["issue_date"]) == (
        "issued",
        "invoice",
        None,
        "2026-10-12",
    )
    assert [(line["name"], line["quantity"]) for line in detail["lines"]] == [("Standard tune-up", "2.000")]
    everything = owner.get("/api/v1/sales/invoices").json()["items"]
    assert sorted(i["bill_to_name"] for i in everything) == ["Ada Example", "Example Courier Co."]  # Ada's is not due


@pytest.mark.parametrize(
    "now",
    [
        THURSDAY,
        datetime(2026, 11, 2, 6, 30, tzinfo=UTC),  # Sunday 23:30 in Denver but already Monday in UTC; DST ended today
        datetime(2027, 1, 5, 8, 0, tzinfo=UTC),  # the order history reaches back into the previous year
        datetime(2027, 3, 15, 2, 0, tzinfo=UTC),  # Sunday evening in Denver, just after the clocks went forward
    ],
)
def test_showcase_dates_follow_the_clock_of_the_seed_run(registry, now) -> None:  # type: ignore[no-untyped-def]
    from app.modules.purchasing import public as purchasing
    from app.platform.db import transaction
    from app.platform.testing.auth import ctx_for

    clock.set_clock(lambda: now)
    seed()
    owner = _owner(registry)
    today = now.astimezone(ZoneInfo("America/Denver")).date()
    late = owner.get(f"{BASE}/late-purchase-orders").json()
    assert [(o["days_late"], o["expected_delivery_date"]) for o in late] == [(4, str(today - timedelta(days=4)))]
    overdue = owner.get("/api/v1/sales/invoices", params={"overdue": "true"}).json()["items"]
    assert [i["due_date"] for i in overdue] == [str(today - timedelta(days=10))]
    request = _open_request(owner)
    assert (request["reply_by"], request["needed_by"]) == (
        str(today + timedelta(days=7)),
        str(today + timedelta(days=11)),
    )
    ranked = owner.get(f"{BASE}/quote-requests/{request['id']}/comparison").json()["ranked"]
    assert [(q["supplier_name"], q["score"]) for q in ranked] == [(DIRECT, 0.85), (WHOLESALE, 0.811)]
    pads = {x["item_name"]: x for x in owner.get("/api/v1/inventory/levels").json()}["Brake pads (pair)"]
    assert (Decimal(pads["on_hand"]), Decimal(pads["reserved"])) == (3, 1)  # history deliveries are used up again
    ctx = ctx_for(CYCLE, ["purchasing.po.read"])
    with transaction(ctx) as db:  # the request is neither due for comparison nor chased yet
        assert purchasing.list_quote_requests_due(db, ctx) == []
        assert purchasing.list_unanswered_invites(db, ctx) == []


def test_reset_restores_live_guarded_cycle_shadow_garden_and_ask_dental(registry: Any, owner_engine: Engine) -> None:
    from app.cli import main

    seed()
    with owner_engine.begin() as db:
        db.execute(text("update autonomy_rules set shadow=true where business_id=:b"), {"b": CYCLE})
        db.execute(
            text("update business_settings set settings=settings - 'ai_reply_guardrail' where business_id=:b"),
            {"b": CYCLE},
        )
    main(["reset-demo", "--yes"])
    with owner_engine.connect() as db:
        rows = db.execute(
            text(
                "select b.slug,r.level,r.shadow,s.settings from businesses b "
                "join autonomy_rules r on r.business_id=b.id join business_settings s on s.business_id=b.id "
                "where r.action_type='conversations.send_reply' order by b.slug"
            )
        ).all()
    by_slug = {r.slug: r for r in rows}
    cycle = by_slug["example-cycle-works"]
    assert cycle.level == "act_notify" and cycle.shadow is False
    assert cycle.settings["outbound_send_delay_seconds"] == 15
    assert "discount" in cycle.settings["ai_reply_guardrail"]
    assert by_slug["example-garden-store"].shadow is True
    assert by_slug["example-dental-clinic"].level == "ask"
