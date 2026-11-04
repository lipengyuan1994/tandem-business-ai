"""U9 orchestration. Purchasing owns facts and execution; the assistant only creates review proposals."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.inventory import public as inventory
from app.modules.org import public as org
from app.modules.purchasing import public as purchasing
from app.platform import clock, jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import DomainError, Invalid

from ..agent.contexts import CtxFactory, background_ctx_factory, staff_ctx_factory
from ..agent.prompts import load_prompt
from ..agent.protocol import AGENT_STEP_ADAPTER, ContextBlock, RunSpec, ToolCallStep
from ..agent.runner import run_agent
from ..errors import AiError
from ..gateway.router import Router
from ..gateway.routes import ModelClass, RouteEntry
from ..gateway.types import ChatRequest, ChatResult
from ..models import AiEffect, AiRun
from ..recording import RunMeta, record_early_result
from ..schemas import Policy, ProcurementRecommendation, SupplierQuoteExtract
from ..structured import complete
from ..tools.base import RunState
from ..tools.common import cite, qty, redact_contacts
from ..tools.registry import execute_tool, get_tool
from .procurement_fake import script_router

REQUEST_TOOLS = ["check_stock", "list_low_stock", "list_suppliers_for_item", "propose_quote_request"]
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+|[ '\u00a0\u202f\u2019]\d{3})*(?!\w|[.,]\d)")


class RequestQuotesOut(BaseModel):
    run_id: uuid.UUID
    proposal_ids: list[uuid.UUID]


class ExtractIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: uuid.UUID
    text: str = Field(min_length=1, max_length=8000)


class ExtractOut(BaseModel):
    run_id: uuid.UUID
    extract: SupplierQuoteExtract
    verified: bool
    unverified_fields: list[str]
    proposal_id: uuid.UUID | None


class SupplierFollowup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)


def _factory(ctx: Context) -> CtxFactory:
    assert ctx.business_id is not None
    if ctx.user_id is not None:
        return staff_ctx_factory(ctx.business_id, ctx.user_id, ctx.permissions, "procurement")
    return background_ctx_factory(ctx.business_id)


def _json(value: Any) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, default=str, ensure_ascii=False)


def request_dates(options: list[purchasing.SupplierOption], timezone: str) -> tuple[date, date]:
    today = clock.now().astimezone(ZoneInfo(timezone)).date()
    needed = today + timedelta(days=max((s.lead_time_days or 0 for s in options if s.has_email), default=0) + 2)
    reply = today
    remaining = 2
    while remaining:
        reply += timedelta(days=1)
        if reply.weekday() < 5:
            remaining -= 1
    return needed, reply


class _RequestGuard(Router):
    """Validate a procurement write before the runner can execute it.

    The shared tool validates domain inputs; this boundary also enforces U9's narrower item/date/supplier limits.
    Invalid JSON enters the runner's existing recorded repair path (including usage and the single-repair limit).
    """

    def __init__(
        self,
        delegate: Router,
        item_id: uuid.UUID,
        quantity: Decimal,
        options: list[purchasing.SupplierOption],
        needed: date,
        reply: date,
    ) -> None:
        self.delegate = delegate
        self.limits = delegate.limits
        self.item_id, self.quantity = item_id, quantity
        self.suppliers = {s.supplier_id for s in options if s.has_email}
        self.needed, self.reply = needed, reply
        self.proposed = False

    def allowed(self, model_class: ModelClass, policy: Policy) -> list[RouteEntry]:
        return self.delegate.allowed(model_class, policy)

    def complete(self, model_class: ModelClass, policy: Policy, req: ChatRequest) -> tuple[ChatResult, RouteEntry]:
        result, entry = self.delegate.complete(model_class, policy, req)
        try:
            step = AGENT_STEP_ADAPTER.validate_json(result.text)
            if isinstance(step, ToolCallStep) and step.tool == "propose_quote_request":
                args = get_tool(step.tool).args_model.model_validate(step.arguments).model_dump(mode="json")
                chosen = [uuid.UUID(s) for s in args["supplier_ids"]]
                if (
                    self.proposed
                    or not step.reason.strip()
                    or not 1 <= len(chosen) <= 3
                    or len(set(chosen)) != len(chosen)
                    or not set(chosen) <= self.suppliers
                    or len(args["lines"]) != 1
                    or args["lines"][0]["item_id"] != str(self.item_id)
                    or Decimal(args["lines"][0]["quantity"]) != self.quantity
                    or args["needed_by"] != self.needed.isoformat()
                    or args["reply_by"] != self.reply.isoformat()
                ):
                    raise ValueError("procurement request violates server facts")
                self.proposed = True
        except ValueError:
            result = result.model_copy(update={"text": "{}"})
        return result, entry


def _skip(factory: CtxFactory, key: str, reason: str, *, api: bool) -> RequestQuotesOut:
    ctx = factory(uuid.uuid4())
    result = record_early_result(
        ctx,
        RunMeta(
            use_case="procurement",
            prompt=load_prompt("procurement_request"),
            trigger_type="api" if api else "job",
            trigger_key=key,
            on_behalf_of_user_id=ctx.on_behalf_of_user_id,
        ),
        needs_human=False,
        result={"skip_reason": reason, "proposal_ids": []},
    )
    return RequestQuotesOut(run_id=result.run_id, proposal_ids=[])


def request_quotes(
    item_id: uuid.UUID, business_id: uuid.UUID, *, ctx: Context | None = None, router: Router | None = None
) -> RequestQuotesOut:
    factory = _factory(ctx) if ctx is not None else background_ctx_factory(business_id)
    read_ctx = factory(uuid.uuid4())
    key = f"procurement:request:{item_id}:{clock.now().astimezone(UTC).date()}"
    reason = None
    with transaction(read_ctx) as db:
        options = purchasing.list_suppliers_for_item(db, read_ctx, item_id)  # validates tenant even if no low stock
        brief = org.get_business_brief(db, read_ctx)
        suggestion = next((s for s in inventory.list_below_reorder(db, read_ctx) if s.item_id == item_id), None)
        if purchasing.find_open_quote_request(db, read_ctx, item_id) is not None:
            reason = "open_request_exists"
        elif approvals.find_open_proposals(
            db, read_ctx, action_types=["purchasing.send_quote_requests"], item_id=item_id
        ):
            reason = "open_proposal_exists"
        elif suggestion is None:
            reason = "not_below_reorder"
        elif not any(s.has_email for s in options):
            reason = "no_supplier_email"
    if reason is not None:
        return _skip(factory, key, reason, api=ctx is not None)
    assert suggestion is not None
    needed, reply = request_dates(options, brief.timezone)
    # Existing purchasing validation requires reply_by <= needed_by. Do not silently change either frozen date rule.
    if reply > needed:
        return _skip(factory, key, "reply_after_needed_by", api=ctx is not None)
    facts = dict(
        item_id=str(item_id),
        item_name=redact_contacts(suggestion.item_name),
        suggested=qty(suggestion.suggested),
        needed_by=needed,
        reply_by=reply,
        suppliers=[s.model_dump(mode="json") for s in options],
    )
    selected_router = script_router(
        router,
        "procurement",
        {
            "item_id": str(item_id),
            "item_ids": [str(item_id)],
            "request_lines": [{"item_id": str(item_id), "quantity": qty(suggestion.suggested)}],
            "supplier_ids": [str(s.supplier_id) for s in options if s.has_email][:3],
            "needed_by": needed.isoformat(),
            "reply_by": reply.isoformat(),
        },
    )
    guarded = (
        _RequestGuard(selected_router, item_id, suggestion.suggested, options, needed, reply)
        if selected_router
        else None
    )
    result = run_agent(
        factory,
        RunSpec(
            use_case="procurement",
            prompt=load_prompt("procurement_request"),
            tools=REQUEST_TOOLS,
            task="Request quotes for this item only. Use the exact suggested quantity and dates. "
            "Choose up to 3 listed suppliers with has_email=true and explain the selection in the step reason.",
            context_blocks=[
                ContextBlock(label="procurement_request", source_id=item_id, text=_json(facts), untrusted=True)
            ],
            trigger_type="api" if ctx is not None else "job",
            trigger_key=key,
        ),
        router=guarded,
    )
    if result.status != "succeeded":
        raise AiError(result.error_code or "ai.invalid_model_output")
    return RequestQuotesOut(run_id=result.run_id, proposal_ids=result.proposal_ids)


def _decimal(raw: str) -> Decimal:
    text = re.sub(r"[ '\u00a0\u202f\u2019$€£¥]", "", raw.strip())
    text = re.sub(r"^(USD|EUR|GBP|JPY|CAD|AUD)\s*|\s*(USD|EUR|GBP|JPY|CAD|AUD)$", "", text, flags=re.I)
    sign = -1 if text.startswith("-") else 1
    text = text.removeprefix("-").removeprefix("+")
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", text):
        raise ValueError("not a supplier number")
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        grouping = "." if decimal == "," else ","
        whole, fraction = text.rsplit(decimal, 1)
        if not re.fullmatch(r"\d{1,3}(?:" + re.escape(grouping) + r"\d{3})+|\d+", whole):
            raise ValueError("invalid grouping")
        text = whole.replace(grouping, "") + "." + fraction
    elif "," in text:
        text = text.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", text) else text.replace(",", ".")
    elif text.count(".") > 1:
        if not re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
            raise ValueError("invalid grouping")
        text = text.replace(".", "")
    try:
        value = Decimal(text) * sign
    except InvalidOperation:
        raise ValueError("not a supplier number") from None
    if not value.is_finite():
        raise ValueError("not a finite supplier number")
    return value


def _source_numbers(text: str) -> set[Decimal]:
    values = set()
    for match in _NUMBER.finditer(text):
        try:
            values.add(_decimal(match.group()))
        except ValueError:
            pass
    return values


def _supplier_data(text: str) -> str:
    # Obvious instruction suffixes cannot provide evidence for numbers. The full original stays fenced for the model.
    return re.sub(
        r"(?is)\b(?:ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|"
        r"set\s+(?:the\s+)?(?:price|lead\s*time)\s+to)\b[^\n]*",
        "",
        text,
    )


def _verify(
    value: SupplierQuoteExtract, request: purchasing.QuoteRequestOut, source: str, default_currency: str
) -> list[str]:
    expected = [line.item_id for line in request.lines]
    if len(value.lines) != len(expected) or {r.item_id for r in value.lines} != set(expected):
        raise ValueError("one line per requested item is required")
    by_item = {line.item_id: line for line in value.lines}
    value.lines = [by_item[item] for item in expected]
    source = _supplier_data(source)
    numbers = _source_numbers(source)
    unverified = []
    for index, line in enumerate(value.lines):
        for name in ("unit_price", "quantity_available"):
            raw = getattr(line, name)
            if raw is None and name != "unit_price":
                continue
            try:
                number = _decimal(raw) if raw is not None else None
                good = number in numbers and number is not None and number >= 0
                if name == "unit_price":
                    good = (
                        good
                        and number is not None
                        and number >= 0
                        and number * 100 == (number * 100).to_integral_value()
                    )
            except ValueError:
                good = False
            if not good:
                setattr(line, name, None)
                unverified.append(f"lines.{index}.{name}")
    if value.lead_time_days is None or value.lead_time_days < 0 or Decimal(value.lead_time_days) not in numbers:
        value.lead_time_days = None
        unverified.append("lead_time_days")
    if value.min_quantity is not None:
        try:
            good = _decimal(value.min_quantity) in numbers and _decimal(value.min_quantity) > 0
        except ValueError:
            good = False
        if not good:
            value.min_quantity = None
            unverified.append("min_quantity")
    if value.valid_until is not None:
        day = value.valid_until
        forms = (day.isoformat(), day.strftime("%d/%m/%Y"), day.strftime("%m/%d/%Y"), day.strftime("%d.%m.%Y"))
        if not any(re.search(r"(?<!\d)" + re.escape(f) + r"(?!\d)", source) for f in forms):
            value.valid_until = None
            unverified.append("valid_until")
    if value.currency is not None:
        symbol = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(default_currency)
        if not (
            re.search(r"\b" + re.escape(value.currency) + r"\b", source, re.I)
            or (value.currency == default_currency and symbol is not None and symbol in source)
        ):
            value.currency = None
            unverified.append("currency")
    if not _source_numbers(value.notes) <= numbers:
        value.notes = ""
        unverified.append("notes")
    return unverified


def _propose(
    factory: CtxFactory,
    run_id: uuid.UUID,
    key: str,
    tool: str,
    args: dict[str, Any],
    evidence: Any,
    source_id: uuid.UUID,
) -> uuid.UUID:
    state = RunState(run_id=run_id, trigger_key=key)
    cite(state, "procurement", source_id, evidence)
    if tool == "propose_po_from_quote":
        # Evidence excerpts are capped. Keep the top and chosen quote components individually reviewable.
        ranked = evidence["ranked"]
        for quote in ranked:
            if quote is ranked[0] or quote["quote_id"] == args["quote_id"]:
                components = {
                    name: quote[name]
                    for name in (
                        "supplier_name",
                        "total_minor",
                        "currency",
                        "lead_time_days",
                        "delivery_date",
                        "days_late",
                        "price_score",
                        "lead_score",
                        "reliability",
                        "orders_count",
                        "score",
                    )
                }
                cite(state, "compare_quotes", uuid.UUID(quote["quote_id"]), components)
    result = execute_tool(factory(run_id), tool, args, [tool], state)
    if not result["ok"]:
        raise DomainError(result["error"], result.get("detail"))
    return uuid.UUID(str(result["data"]["proposal_id"]))


@contextmanager
def _proposal_lock(ctx: Context, key: str) -> Iterator[Session]:
    """Serialize the post-model check and tool transaction; never hold this lock across a model call."""
    lock_id = int.from_bytes(hashlib.sha256(f"{ctx.business_id}:{key}".encode()).digest()[:8], signed=True)
    with transaction(ctx) as db:
        db.execute(select(func.pg_advisory_xact_lock(lock_id)))
        yield db


def _recent_followup(db: Session, business_id: uuid.UUID, key: str) -> bool:
    # Base-row created_at uses database wall time. Runs use the platform clock, including frozen/replayed time.
    return (
        db.scalar(
            select(AiEffect.id)
            .join(AiRun, (AiRun.id == AiEffect.run_id) & (AiRun.business_id == AiEffect.business_id))
            .where(
                AiEffect.business_id == business_id,
                AiEffect.trigger_key.startswith(key + ":"),
                AiRun.finished_at > clock.now() - timedelta(days=3),
            )
            .limit(1)
        )
        is not None
    )


def extract_reply(ctx: Context, request_id: uuid.UUID, body: ExtractIn, *, router: Router | None = None) -> ExtractOut:
    factory = _factory(ctx)
    read_ctx = factory(uuid.uuid4())
    with transaction(read_ctx) as db:
        request = purchasing.get_quote_request(db, read_ctx, request_id)
        currency = org.get_business_brief(db, read_ctx).currency
    if body.supplier_id not in {s.supplier_id for s in request.suppliers}:
        raise Invalid("purchasing.supplier_not_invited")
    fields: list[str] = []

    def validate(value: SupplierQuoteExtract) -> None:
        fields[:] = _verify(value, request, body.text, currency)
        # Bound every convertible value using the existing propose-tool schema, before recording a successful run.
        if all(line.unit_price is not None for line in value.lines) and value.lead_time_days is not None:
            get_tool("propose_supplier_quote").args_model.model_validate(
                _quote_args(request_id, body.supplier_id, value)
            )

    key = f"procurement:extract:{request_id}:{body.supplier_id}:{uuid.uuid4()}"
    run_id, value = complete(
        factory,
        use_case="procurement",
        prompt=load_prompt("supplier_quote_extract"),
        facts=[
            ContextBlock(
                label="request_lines",
                source_id=request_id,
                text=_json([r.model_dump(mode="json") for r in request.lines]),
                untrusted=True,
            ),
            ContextBlock(label="supplier_reply", source_id=body.supplier_id, text=body.text, untrusted=True),
        ],
        output_model=SupplierQuoteExtract,
        trigger_type="api",
        trigger_key=key,
        router=script_router(
            router,
            "supplier_quote_extract",
            {
                "extract_lines": [{"item_id": str(r.item_id), "unit_price": None} for r in request.lines],
            },
        ),
        validate=validate,
    )
    verified = value.lead_time_days is not None and all(line.unit_price is not None for line in value.lines)
    proposal_id = None
    if verified:
        proposal_id = _propose(
            factory,
            run_id,
            key,
            "propose_supplier_quote",
            _quote_args(request_id, body.supplier_id, value),
            {"supplier_reply": redact_contacts(body.text, 450)},
            request_id,
        )
    return ExtractOut(
        run_id=run_id, extract=value, verified=verified, unverified_fields=fields, proposal_id=proposal_id
    )


def _quote_args(request_id: uuid.UUID, supplier_id: uuid.UUID, value: SupplierQuoteExtract) -> dict[str, Any]:
    return dict(
        quote_request_id=str(request_id),
        supplier_id=str(supplier_id),
        lines=[
            dict(
                item_id=str(r.item_id),
                unit_price_minor=int(_decimal(r.unit_price or "0") * 100),
                quantity_available=str(_decimal(r.quantity_available)) if r.quantity_available is not None else None,
            )
            for r in value.lines
        ],
        currency=value.currency,
        lead_time_days=value.lead_time_days,
        min_quantity=str(_decimal(value.min_quantity)) if value.min_quantity is not None else None,
        valid_until=value.valid_until.isoformat() if value.valid_until is not None else None,
        notes=value.notes or None,
    )


def validate_recommendation(value: ProcurementRecommendation, comparison: purchasing.QuoteComparison) -> None:
    chosen = next((q for q in comparison.ranked if q.quote_id == value.quote_id), None)
    if chosen is None:
        raise ValueError("recommendation must be a complete eligible quote")
    # IDs and names are not numerical evidence. Only the deterministic comparison components may justify claims.
    facts = [
        {k: v for k, v in q.model_dump(mode="json").items() if k not in {"quote_id", "supplier_id", "supplier_name"}}
        for q in comparison.ranked
    ]
    if not _source_numbers(value.explanation) <= _source_numbers(_json(facts)):
        raise ValueError("explanation contains numbers absent from comparison")
    top = comparison.ranked[0]
    if chosen.quote_id == top.quote_id:
        return
    explanation = value.explanation.lower()
    components = [
        (chosen.total_minor < top.total_minor, ("price", "total", "cost"), chosen.total_minor),
        (chosen.lead_time_days < top.lead_time_days, ("lead", "delivery", "days"), chosen.lead_time_days),
        (chosen.days_late < top.days_late, ("late", "needed", "delivery"), chosen.days_late),
        (chosen.reliability > top.reliability, ("reliability", "on-time", "on time"), chosen.reliability),
    ]
    if not any(
        better and any(word in explanation for word in words) and Decimal(str(number)) in _source_numbers(explanation)
        for better, words, number in components
    ):
        raise ValueError("alternative requires a cited component where it is better than the top quote")


def compare(request_id: uuid.UUID, business_id: uuid.UUID, *, router: Router | None = None) -> uuid.UUID | None:
    factory = background_ctx_factory(business_id)
    read_ctx = factory(uuid.uuid4())
    with transaction(read_ctx) as db:
        comparison = purchasing.compare_quotes(db, read_ctx, request_id)
        request = purchasing.get_quote_request(db, read_ctx, request_id)
    if request.status != "sent" or not comparison.ranked:
        return None
    key = f"procurement:compare:{request_id}"
    # Stable request effect key makes retries (scan and all-answered event) reuse one PO proposal.
    with transaction(read_ctx) as db:
        existing = db.scalar(
            select(AiEffect.ref_id)
            .where(AiEffect.business_id == business_id, AiEffect.trigger_key == key, AiEffect.kind == "proposal")
            .limit(1)
        )
    if existing is not None:
        return existing
    run_id, value = complete(
        factory,
        use_case="procurement",
        prompt=load_prompt("procurement_compare"),
        facts=[
            ContextBlock(
                label="quote_comparison", source_id=request_id, text=comparison.model_dump_json(), untrusted=True
            ),
            ContextBlock(label="needed_by", source_id=request_id, text=request.needed_by.isoformat(), untrusted=False),
        ],
        output_model=ProcurementRecommendation,
        trigger_type="job",
        trigger_key=key,
        router=script_router(router, "procurement_compare", {"top_quote_id": str(comparison.ranked[0].quote_id)}),
        validate=lambda value: validate_recommendation(value, comparison),
    )
    # Recheck facts and the request effect after the call, serializing concurrent compare jobs.
    with _proposal_lock(factory(run_id), key) as db:
        existing = db.scalar(
            select(AiEffect.ref_id)
            .where(AiEffect.business_id == business_id, AiEffect.trigger_key == key, AiEffect.kind == "proposal")
            .limit(1)
        )
        if existing is not None:
            return existing
        current = purchasing.compare_quotes(db, factory(run_id), request_id)
        if current != comparison:
            raise AiError("ai.invalid_model_output", detail="Comparison changed; review fresh quotes")
        validate_recommendation(value, current)
        return _propose(
            factory,
            run_id,
            key,
            "propose_po_from_quote",
            dict(quote_request_id=str(request_id), quote_id=str(value.quote_id)),
            comparison.model_dump(mode="json"),
            request_id,
        )


def _followup(
    business_id: uuid.UUID, target: purchasing.LatePO | purchasing.UnansweredInvite, *, router: Router | None
) -> uuid.UUID | None:
    factory = background_ctx_factory(business_id)
    ctx = factory(uuid.uuid4())
    is_po = isinstance(target, purchasing.LatePO)
    key = f"procurement:followup:{'po' if is_po else 'invite'}:{target.id}"
    since = clock.now() - timedelta(days=3)
    with transaction(ctx) as db:
        recent = _recent_followup(db, business_id, key)
    if recent or (target.last_followup_at is not None and target.last_followup_at > since):
        return None
    data = target.model_dump(mode="json")
    facts = _json(data)

    def validate(value: SupplierFollowup) -> None:
        if not _source_numbers(value.body) <= _source_numbers(facts):
            raise ValueError("followup invents numbers")
        get_tool("propose_supplier_followup").args_model.model_validate({**args, "body": value.body})

    args = (
        {"purchase_order_id": str(target.id)}
        if isinstance(target, purchasing.LatePO)
        else {"quote_request_id": str(target.quote_request_id), "supplier_id": str(target.supplier_id)}
    )
    run_id, value = complete(
        factory,
        use_case="procurement",
        prompt=load_prompt("supplier_followup"),
        facts=[ContextBlock(label="followup_facts", source_id=target.id, text=facts, untrusted=True)],
        output_model=SupplierFollowup,
        trigger_type="cron",
        trigger_key=key + f":{clock.now().isoformat()}",
        router=script_router(router, "supplier_followup", {}),
        validate=validate,
    )
    # A rolling window and a post-model lock suppress both pending drafts and concurrent scans with different wording.
    with _proposal_lock(factory(run_id), key) as db:
        if _recent_followup(db, business_id, key):
            return None
        return _propose(
            factory,
            run_id,
            key + f":{clock.now().astimezone(UTC).date()}",
            "propose_supplier_followup",
            {**args, "body": value.body},
            data,
            target.id,
        )


def scan(business_id: uuid.UUID, *, router: Router | None = None) -> None:
    ctx = background_ctx_factory(business_id)(uuid.uuid4())
    with transaction(ctx) as db:
        for due in purchasing.list_quote_requests_due(db, ctx):
            jobs.enqueue(
                db,
                "ai.procurement_compare",
                {"quote_request_id": str(due.quote_request_id)},
                business_id=business_id,
                dedupe_key=f"procurement_compare:{due.quote_request_id}",
            )
        late = purchasing.list_late_purchase_orders(db, ctx)
        unanswered = purchasing.list_unanswered_invites(db, ctx)
    targets: list[purchasing.LatePO | purchasing.UnansweredInvite] = [*late, *unanswered]
    for target in targets:
        _followup(business_id, target, router=router)
