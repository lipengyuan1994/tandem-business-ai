"""Payment jobs and proposal transaction regressions; no email is sent."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text

from app.modules.ai.agent.contexts import background_ctx_factory
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.ai.tests.test_payment_followup import GOOD, proposals, second_invoice
from app.modules.ai.tests.tool_helpers import seed_demo
from app.modules.ai.tools.base import RunState
from app.modules.ai.tools.registry import execute_tool
from app.modules.ai.usecases import payment_followup
from app.platform import clock, jobs
from app.platform.db import transaction
from app.platform.models import Job


def test_payment_rechecks_cooldown_in_proposal_transaction(registry, owner_engine, monkeypatch):
    demo = seed_demo(owner_engine)
    execute = payment_followup.execute_tool

    def reminder_just_sent(ctx, *args, **kwargs):
        with owner_engine.begin() as conn:
            conn.execute(
                text("update invoices set last_reminder_at=:at, version=version+1 where business_id=:b"),
                {"at": clock.now(), "b": demo.business},
            )
        return execute(ctx, *args, **kwargs)

    monkeypatch.setattr(payment_followup, "execute_tool", reminder_just_sent)
    assert (
        payment_followup.run(
            demo.business, router=fake_router_by_class(writer=FakeGateway([json.dumps({"text": GOOD})]))
        )
        == []
    )
    assert not proposals(owner_engine, demo.business)


def test_payment_proposal_checks_expected_version_and_serializes_duplicates(registry, owner_engine):
    demo = seed_demo(owner_engine)
    import uuid

    from app.modules.sales import public as sales

    factory = background_ctx_factory(demo.business, use_case="payment_followup")
    with transaction(factory(uuid.uuid4())) as db:
        [invoice] = sales.list_overdue_invoices(db, factory(uuid.uuid4()))

    def propose(index, version):
        from app.modules.ai.tests.helpers import insert_run

        ctx = factory(uuid.uuid4())
        run_id = insert_run(ctx, use_case="payment_followup")
        state = RunState(run_id=run_id, trigger_key=f"competing:{index}", server_facts={"invoice_version": version})
        return execute_tool(
            ctx,
            "propose_payment_reminder",
            {"invoice_id": str(invoice.id), "text": GOOD},
            ["propose_payment_reminder"],
            state,
        )

    assert propose("stale", invoice.version - 1)["error"] == "ai.invoice_changed"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda i: propose(i, invoice.version), range(2)))
    assert sum(r["ok"] for r in results) == 1
    assert len(proposals(owner_engine, demo.business)) == 1


def test_payment_scan_only_enqueues_deduplicated_isolated_jobs(registry, owner_engine, monkeypatch):
    demo = seed_demo(owner_engine)
    second_invoice(owner_engine, demo.business)
    monkeypatch.setattr(
        payment_followup, "complete", lambda *a, **k: (_ for _ in ()).throw(AssertionError("scan inferred"))
    )
    monkeypatch.setattr(payment_followup, "_LIMIT", 1)  # exercise every page, including an equal due-date tie
    payment_followup.scan(demo.business)
    payment_followup.scan(demo.business)
    with transaction(background_ctx_factory(demo.business)(None)) as db:
        queued = list(db.scalars(select(Job).where(Job.kind == "ai.payment_followup")))
        assert len(queued) == 2
        assert len({j.payload["invoice_id"] for j in queued}) == 2
    assert registry.jobs["ai.payment_followup"].transactional is False
    seen = []

    def target(invoice_id, business_id):
        seen.append(invoice_id)
        if len(seen) == 1:
            raise RuntimeError("one target failed")

    monkeypatch.setattr(payment_followup, "run_invoice", target)
    jobs.run_pending(registry)
    assert len(set(seen)) == 2
    with owner_engine.connect() as conn:
        assert set(conn.execute(text("select status from jobs where kind='ai.payment_followup'")).scalars()) == {
            "queued",
            "succeeded",
        }


def test_payment_generation_uses_point_reads(registry, owner_engine, monkeypatch):
    demo = seed_demo(owner_engine)
    from app.modules.sales import public as sales

    original = sales.list_overdue_invoices
    calls = []

    def read(*a, **k):
        calls.append(1)
        return original(*a, **k)

    monkeypatch.setattr(sales, "list_overdue_invoices", read)
    assert (
        len(
            payment_followup.run(
                demo.business, router=fake_router_by_class(writer=FakeGateway([json.dumps({"text": GOOD})]))
            )
        )
        == 1
    )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "text_value",
    ["Please pay half the balance.", "A dozen invoices are overdue.", "Please pay a quarter of the amount."],
)
def test_payment_writer_rejects_unsupported_word_quantities(registry, owner_engine, text_value):
    import uuid

    from app.modules.ai.schemas import PaymentReminderDraft
    from app.modules.sales import public as sales

    demo = seed_demo(owner_engine)
    ctx = background_ctx_factory(demo.business, use_case="payment_followup")(uuid.uuid4())
    with transaction(ctx) as db:
        [invoice] = sales.list_overdue_invoices(db, ctx)
    with pytest.raises(ValueError):
        payment_followup._validate(PaymentReminderDraft(text=text_value), invoice)
