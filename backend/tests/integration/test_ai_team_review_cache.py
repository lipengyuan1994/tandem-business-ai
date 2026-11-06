"""Legacy interrupted extraction caches must not override the resolved request's lineage.

These branch tests use no database; the companion HTTP regressions exercise admission against PostgreSQL.
"""

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.ai import chains
from app.modules.ai.errors import AiError
from app.modules.ai.models import AiRun
from app.modules.ai.usecases import procurement
from app.modules.ai.usecases.summary import Operation
from app.platform.context import system_context


@pytest.mark.parametrize("condition", ["stopped", "depth", "wrong_cause", "matching"])
def test_interrupted_extraction_cache_requires_resolved_lineage(monkeypatch, condition):
    business, root, producer, request_id, supplier, item = (uuid4() for _ in range(6))
    parent_depth = 6 if condition == "depth" else 2
    source = replace(
        system_context(business),
        correlation_id=root,
        causation_id=producer,
        agent_key="purchasing",
        chain_depth=parent_depth,
    )
    cached_id, new_id = uuid4(), uuid4()
    value = {"lines": [{"item_id": str(item), "unit_price": "11.40"}], "currency": "USD", "lead_time_days": 3}
    cached = SimpleNamespace(
        id=cached_id,
        status="succeeded",
        result=value,
        agent_key="purchasing",
        correlation_id=uuid4() if condition == "stopped" else root,
        chain_depth=1 if condition == "depth" else parent_depth + 1,
        causation_id=None if condition == "wrong_cause" else producer,
    )
    operation = SimpleNamespace(
        run_id=cached_id, details={"verified": True, "unverified_fields": [], "default_currency": "USD"}
    )

    class Database:
        def get(self, model, ident):
            assert model in (AiRun, Operation)
            return cached if model is AiRun else operation

    monkeypatch.setattr(procurement, "transaction", lambda ctx: nullcontext(Database()))
    monkeypatch.setattr(chains, "subject_context", lambda *args, **kwargs: source)
    monkeypatch.setattr(
        procurement.purchasing,
        "get_quote_request",
        lambda *args: SimpleNamespace(suppliers=[SimpleNamespace(supplier_id=supplier)], lines=[]),
    )
    monkeypatch.setattr(procurement.org, "get_business_brief", lambda *args: SimpleNamespace(currency="USD"))
    completions, proposals = [], []

    def complete(factory, **kwargs):
        ctx = factory(new_id)
        assert (ctx.correlation_id, ctx.causation_id, ctx.chain_depth) == (root, producer, parent_depth + 1)
        completions.append(ctx)
        if condition in {"stopped", "depth"}:
            raise AiError("ai.chain_call_limit" if condition == "stopped" else "ai.chain_depth_exceeded")
        return new_id, procurement.SupplierQuoteExtract.model_validate(value)

    def propose(factory, run_id, *args):
        proposals.append(run_id)
        return uuid4()

    monkeypatch.setattr(procurement, "complete", complete)
    monkeypatch.setattr(procurement, "_propose", propose)
    monkeypatch.setattr(procurement, "_quote_args", lambda *args: {})
    body = procurement.ExtractIn(supplier_id=supplier, text="Unit price USD 11.40. Delivery 3 days.")
    if condition in {"stopped", "depth"}:
        with pytest.raises(AiError) as error:
            procurement._extract_reply(system_context(business), request_id, body, router=None, operation_id=uuid4())
        assert error.value.code == ("ai.chain_call_limit" if condition == "stopped" else "ai.chain_depth_exceeded")
        assert len(completions) == 1 and proposals == []
    else:
        out = procurement._extract_reply(system_context(business), request_id, body, router=None, operation_id=uuid4())
        expected = cached_id if condition == "matching" else new_id
        assert out.run_id == expected and proposals == [expected]
        assert len(completions) == (0 if condition == "matching" else 1)
