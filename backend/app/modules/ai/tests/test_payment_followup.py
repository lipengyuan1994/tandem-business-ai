"""AI-15: real invoice reads and proposals, never a live writer or delivery."""

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Engine, text

from app.modules.ai.agent.contexts import background_ctx_factory
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.ai.tests.tool_helpers import SEED_NOW, seed_demo
from app.modules.ai.usecases import payment_followup
from app.platform import clock
from app.platform.registry import Registry

GOOD = "Hello, your invoice for 190.00 USD was due on 2026-10-26. Please let us know if you need help. Thank you!"


def proposals(engine: Engine, business):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "select p.id, p.status, p.origin_kind, p.ai_run_id, r.payload, r.evidence "
                "from action_proposals p join proposal_revisions r on r.id=p.current_revision_id "
                "where p.business_id=:b and p.action_type='sales.send_payment_reminder'"
            ),
            {"b": business},
        ).all()


def test_payment_followup_one_proposal_per_overdue_invoice_with_cooldown(registry, owner_engine):
    demo = seed_demo(owner_engine)
    gw = FakeGateway([json.dumps({"text": GOOD})])
    router = fake_router_by_class(writer=gw)
    ids = payment_followup.run(demo.business, router=router)
    assert len(ids) == 1
    [p] = proposals(owner_engine, demo.business)
    assert p.id == ids[0] and p.status == "pending_review" and p.origin_kind == "assistant"
    assert p.payload["body"] == GOOD and p.payload["invoice_version"] == 2
    assert "fleet@example.com" not in json.dumps(p.evidence)
    assert p.ai_run_id is not None
    assert payment_followup.run(demo.business, router=router) == []
    clock.set_clock(lambda: SEED_NOW + timedelta(days=1))
    assert payment_followup.run(demo.business, router=router) == []  # pending across days
    assert len(gw.calls) == 1
    facts = gw.calls[0].messages[1].content
    assert '<untrusted source="invoice"' in facts
    assert "fleet@example.com" not in facts and "190.00 USD" in facts
    # A sent reminder is the authoritative cooldown. Do not execute or send anything in this test.
    with owner_engine.begin() as conn:
        conn.execute(text("update action_proposals set status='succeeded' where id=:p"), {"p": p.id})
        conn.execute(
            text("update invoices set last_reminder_at=:at where id=:i"),
            {"at": SEED_NOW, "i": p.payload["invoice_id"]},
        )
    clock.set_clock(lambda: SEED_NOW + timedelta(days=6, hours=23))
    assert payment_followup.run(demo.business, router=router) == []
    clock.set_clock(lambda: SEED_NOW + timedelta(days=7))
    gw2 = FakeGateway([json.dumps({"text": GOOD})])
    assert len(payment_followup.run(demo.business, router=fake_router_by_class(writer=gw2))) == 1
    assert len(proposals(owner_engine, demo.business)) == 2


@pytest.mark.parametrize(
    "bad",
    [
        "Please pay 191.00 USD.",
        "Please pay 190.00 EUR.",
        "Please pay $10.00.",
        "Please pay 190.00 USD by 2026-10-25.",
        "Your invoice was due on 2026-26-10.",
        "Your invoice was due on October 25, 2026.",
        "Please pay ten dollars tomorrow.",
    ],
)
def test_payment_followup_rejects_wrong_amounts(registry, owner_engine, bad):
    demo = seed_demo(owner_engine)
    gw = FakeGateway([json.dumps({"text": bad})] * 2)
    with pytest.raises(Exception) as exc:
        payment_followup.run(demo.business, router=fake_router_by_class(writer=gw))
    assert getattr(exc.value, "code", None) == "ai.invalid_model_output"
    assert len(gw.calls) == 2 and proposals(owner_engine, demo.business) == []


def test_payment_followup_repairs_wrong_amount(registry, owner_engine):
    demo = seed_demo(owner_engine)
    gw = FakeGateway([json.dumps({"text": "Pay 999.00 USD."}), json.dumps({"text": GOOD})])
    assert len(payment_followup.run(demo.business, router=fake_router_by_class(writer=gw))) == 1
    assert len(gw.calls) == 2


def test_payment_followup_skips_without_email(registry, owner_engine):
    demo = seed_demo(owner_engine)
    with owner_engine.begin() as conn:
        conn.execute(
            text("update invoices set bill_to_email=null where business_id=:b"),
            {"b": demo.business},
        )
    gw = FakeGateway([])
    assert payment_followup.run(demo.business, router=fake_router_by_class(writer=gw)) == []
    assert not gw.calls and not proposals(owner_engine, demo.business)


def test_payment_followup_rechecks_after_writer(registry, owner_engine):
    demo = seed_demo(owner_engine)

    def paid_during_generation(request):
        with owner_engine.begin() as conn:
            conn.execute(
                text("update invoices set status='paid' where business_id=:b"),
                {"b": demo.business},
            )
        return json.dumps({"text": GOOD})

    assert (
        payment_followup.run(demo.business, router=fake_router_by_class(writer=FakeGateway(paid_during_generation)))
        == []
    )
    assert not proposals(owner_engine, demo.business)


def test_payment_followup_daily_job_registration(registry: Registry):
    job = registry.jobs["ai.payment_followup_scan"]
    assert job.transactional is False
    [cron] = [c for c in registry.cron if c.name == "ai-payment-followup-scan"]
    assert cron.per_business is True and cron.every_seconds == 86400
    assert cron.job_kind == "ai.payment_followup_scan"


def second_invoice(owner_engine, business):
    with owner_engine.begin() as conn:
        invoice = dict(
            conn.execute(
                text("select * from invoices where business_id=:b and status='issued' and due_date < :today"),
                {"b": business, "today": SEED_NOW.date()},
            )
            .one()
            ._mapping
        )
        invoice["id"] = uuid.uuid4()
        invoice["number"] = "INV-2026-0099"
        columns = ", ".join(invoice)
        values = ", ".join(f":{column}" for column in invoice)
        conn.execute(text(f"insert into invoices ({columns}) values ({values})"), invoice)  # noqa: S608
    return invoice["id"]


def test_payment_followup_handles_each_overdue_invoice(registry, owner_engine):
    demo = seed_demo(owner_engine)
    second_invoice(owner_engine, demo.business)
    gw = FakeGateway([json.dumps({"text": GOOD})] * 2)
    ids = payment_followup.run(demo.business, router=fake_router_by_class(writer=gw))
    assert len(ids) == 2 and len(gw.calls) == 2
    assert len({p.payload["invoice_id"] for p in proposals(owner_engine, demo.business)}) == 2


def test_payment_followup_skips_pending_reminder_with_arbitrary_title(registry, owner_engine):
    from app.modules.ai.tests.tool_helpers import assistant
    from app.modules.approvals import public as approvals
    from app.modules.sales import public as sales
    from app.platform.db import transaction

    demo = seed_demo(owner_engine)
    ctx, _ = assistant(
        demo.business,
        background_ctx_factory(demo.business, use_case="payment_followup")(uuid.uuid4()).permissions,
    )
    with transaction(ctx) as db:
        [invoice] = sales.list_overdue_invoices(db, ctx)
        approvals.create_proposal(
            db,
            ctx,
            action_type="sales.send_payment_reminder",
            payload={
                "invoice_id": str(invoice.id),
                "invoice_version": invoice.version,
                "body": "Please check this open invoice.",
            },
            title="Finance team reminder",
        )
    gw = FakeGateway([])
    assert payment_followup.run(demo.business, router=fake_router_by_class(writer=gw)) == []
    assert not gw.calls and len(proposals(owner_engine, demo.business)) == 1


def test_payment_followup_pending_staff_reminder_does_not_block_other_invoice(registry, owner_engine):
    from app.modules.ai.tests.tool_helpers import assistant
    from app.modules.approvals import public as approvals
    from app.modules.sales import public as sales
    from app.platform.db import transaction

    demo = seed_demo(owner_engine)
    other_invoice = second_invoice(owner_engine, demo.business)
    ctx, _ = assistant(
        demo.business, background_ctx_factory(demo.business, use_case="payment_followup")(uuid.uuid4()).permissions
    )
    with transaction(ctx) as db:
        invoice = next(i for i in sales.list_overdue_invoices(db, ctx) if i.id != other_invoice)
        approvals.create_proposal(
            db,
            ctx,
            action_type="sales.send_payment_reminder",
            payload={
                "invoice_id": str(invoice.id),
                "invoice_version": invoice.version,
                "body": "Please check this open invoice.",
            },
            title="Finance team reminder",
        )
    gw = FakeGateway([json.dumps({"text": GOOD})])
    ids = payment_followup.run(demo.business, router=fake_router_by_class(writer=gw))
    assert len(ids) == 1 and len(gw.calls) == 1
    generated = next(p for p in proposals(owner_engine, demo.business) if p.id == ids[0])
    assert generated.payload["invoice_id"] == str(other_invoice)
