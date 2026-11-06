"""Seeded application integration and in-memory evaluation routing.

SQL below is fixture setup/result observation, using the isolated test owner
connection. Application reads and writes go through public APIs and real use cases.
No worker is started and seeded notifications remain simulated.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.engine import make_url

from app import seed as seed_module
from app.modules.ai.agent.protocol import FinalStep
from app.modules.ai.gateway import router as router_module
from app.modules.ai.gateway.anthropic import AnthropicAdapter
from app.modules.ai.gateway.openai_compat import OpenAICompatAdapter
from app.modules.ai.gateway.router import Router
from app.modules.ai.gateway.routes import ModelClass, RouteEntry, load_routes_file
from app.modules.ai.gateway.types import Adapter
from app.modules.ai.models import AiRun, AiRunStep
from app.modules.ai.schemas import SupplierQuoteExtract, ThreadSummary
from app.modules.approvals import public as approvals
from app.modules.catalog import public as catalog
from app.modules.conversations import public as conversations
from app.modules.crm import public as crm
from app.modules.identity import public as identity
from app.modules.purchasing import public as purchasing
from app.platform import clock
from app.platform.context import Context, system_context
from app.platform.db import configure_engine, transaction
from app.platform.ids import fixture_id
from evals.runner import Case, Diagnostics, Outcome, Proposal, SkipCase, exception_fields

if TYPE_CHECKING:
    from app.modules.ai.usecases.procurement import ExtractOut

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CLASS: dict[str, ModelClass] = {
    "intake": "agent",
    "injection": "agent",
    "timezones": "agent",
    "copilot": "agent",
    "summary": "fast_extract",
    "procurement": "fast_extract",
    "grounding": "grounding",
    "dictation": "writer",
    "customer_care": "writer",
}


def procurement_adapter(
    ctx: Context, request_id: UUID, supplier_id: UUID, supplier_text: str, *, router: Router | None = None
) -> ExtractOut:
    """Only this binding changes if the concurrently built AI-12 interface changes."""
    name = "app.modules.ai.usecases.procurement"
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        raise SkipCase("procurement unavailable: AI-12 extract_reply/ExtractIn has not landed") from exc
    if not all(callable(getattr(module, n, None)) for n in ("ExtractIn", "extract_reply")):
        raise SkipCase("procurement unavailable: AI-12 extract_reply/ExtractIn has not landed")
    result: ExtractOut = module.extract_reply(
        ctx,
        request_id,
        module.ExtractIn(supplier_id=supplier_id, text=supplier_text),
        router=router,
    )
    return result


def _reference_names(document: dict[str, Any], business: str) -> dict[str, set[str]]:
    tables = document["tables"]
    names = {
        "customer": {row["display_name"] for row in tables["customers"] if row["business_id"] == business},
        "resource": {row["name"] for row in tables["resources"] if row["business_id"] == business},
        "business": {row["name"] for row in tables["businesses"]},
        "supplier": set(seed_module.QUOTE_SUPPLIERS) | {"Example Tyre Co."} if business == "biz-cycle" else set(),
    }
    for kind in ("service", "item"):
        names[kind] = {
            row["name"]
            for row in tables["catalog_items"]
            if row["business_id"] == business and (kind == "item" or row["kind"] == "service")
        }
    return names


def validate_cases(cases: list[Case], path: Path = seed_module.DEFAULT_FIXTURES) -> None:
    """Preflight every payload and explicit name reference before spending any tokens."""
    document = json.loads(path.read_text())
    if document.get("synthetic") is not True:
        raise ValueError("evaluation fixtures must be synthetic")
    businesses = {b["id"] for b in document["tables"]["businesses"]}
    for case in cases:
        if case.business not in businesses:
            raise ValueError(f"{case.id}: unknown seed business")
        allowed = {
            "channel",
            "visitor",
            "message",
            "operation",
            "quote_request",
            "supplier",
            "references",
            "knowledge_injection",
        }
        if case.suite == "dictation":
            allowed |= {"dictionary", "expected_text"}
        if case.suite == "customer_care":
            allowed |= {"facts", "kind", "minimal", "customer_notes"}
        if case.suite == "grounding":
            allowed |= {"articles", "db_facts"}
        if set(case.input) - allowed or not isinstance(case.input.get("message"), str) or not case.input["message"]:
            raise ValueError(f"{case.id}: invalid input payload")
        if (attack := case.input.get("knowledge_injection")) is not None:
            articles = document["tables"].get("kb_articles", [])
            if (
                not isinstance(attack, dict)
                or set(attack) != {"slug", "text"}
                or not isinstance(attack["text"], str)
                or not 1 <= len(attack["text"]) <= 1000
                or not any(
                    a["business_id"] == case.business and a["slug"] == attack["slug"] and a["visibility"] == "public"
                    for a in articles
                )
            ):
                raise ValueError(f"{case.id}: invalid knowledge injection fixture")
        if case.suite == "dictation":
            terms = case.input.get("dictionary", [])
            if (
                not isinstance(terms, list)
                or len(terms) > 200
                or not all(isinstance(t, str) and len(t) <= 200 for t in terms)
            ):
                raise ValueError(f"{case.id}: invalid dictionary")
        visitor = case.input.get("visitor", {})
        email = visitor.get("email")
        if email is not None and not email.endswith("@example.com"):
            raise ValueError(f"{case.id}: visitor email must use example.com")
        names = _reference_names(document, case.business)
        for proposal in (p for alternative in case.expect.proposal_sets for p in alternative):
            fields = [(k, v) for k, v in proposal.payload.items() if k in names]
            fields += [("item", line["item"]) for line in proposal.payload.get("lines", []) if "item" in line]
            for kind, name in fields:
                if name not in names[kind]:
                    raise ValueError(f"{case.id}: unknown {kind} name {name}")
        for reference in case.input.get("references", []):
            available = _reference_names(document, reference.get("business", case.business))
            if reference.get("name") not in available.get(reference.get("kind"), set()):
                raise ValueError(f"{case.id}: unknown seed reference {reference.get('name')}")
        if case.suite == "procurement":
            if (
                case.input.get("operation") != "extract"
                or case.input.get("supplier") not in names["supplier"]
                or case.input.get("quote_request") != {"item": "Brake pads (pair)"}
            ):
                raise ValueError(f"{case.id}: invalid seeded extraction references")


def build_eval_router(
    suite: str,
    model_class: str | None,
    provider: str | None,
    model: str | None,
    *,
    source: Router | None = None,
) -> tuple[Router, bool]:
    """Copy configuration; never edit routes or accept a credential on the CLI."""
    target = DEFAULT_CLASS[suite]
    if model_class is not None and model_class != target:
        raise ValueError(f"suite {suite} uses model class {target}")
    if source is None:
        path = Path(os.environ.get("AI_ROUTES_FILE", str(REPO / "deploy/config/ai-routes.example.yaml")))
        routes = load_routes_file(path)
        adapters: dict[str, Adapter] = {}
        for name, cfg in routes.providers.items():
            # Deliberately bypass cached settings/.env credentials: live eval keys come only from this process's env.
            key = os.environ.get(cfg.api_key_env, "") if cfg.api_key_env else ""
            if cfg.kind == "fake" or ((cfg.api_key_env or cfg.hosted) and not key):
                continue
            if cfg.kind == "anthropic":
                adapters[name] = AnthropicAdapter(name, cfg.base_url, key or None)
            else:
                adapters[name] = OpenAICompatAdapter(name, cfg.base_url, key or None, local=not cfg.hosted)
    else:
        routes, adapters = source.routes, source.adapters
    routes = routes.model_copy(deep=True)
    entries = routes.routes.get(target, [])
    candidate = next((e for e in entries if provider is None or e.provider == provider), None)
    if provider is not None and provider not in routes.providers:
        raise ValueError("requested provider is not configured")
    if candidate is None and (not provider or not model):
        raise ValueError("provider/model pair has no configured route")
    if candidate is None:
        candidate = RouteEntry(provider=provider or "", model=model or "")
    chosen_provider = provider or candidate.provider
    chosen_model = model or candidate.model
    # Unknown model prices remain explicitly unknown, rather than borrowing another model's price.
    priced_entry = next(
        (
            e
            for group in routes.routes.values()
            for e in group
            if e.provider == chosen_provider and e.model == chosen_model
        ),
        None,
    )
    chosen = (priced_entry or RouteEntry(provider=chosen_provider, model=chosen_model)).model_copy(deep=True)
    if provider is not None or model is not None:
        routes.routes[target] = [chosen]
    if source is None:
        selected = routes.routes.get(target, [])
        if any(routes.providers[e.provider].kind == "fake" for e in selected):
            raise ValueError("live eval requires a real provider; FakeGateway is injected only by unit tests")
        if not any(e.provider in adapters for e in selected):
            raise ValueError("no selected provider has an environment key or usable local adapter")
    priced = chosen.price_in_per_m > 0 and chosen.price_out_per_m > 0
    return Router(routes, adapters.copy()), priced


@dataclass
class CaseState:
    business_id: UUID
    business_ids: list[UUID]
    ctx: Context
    timezone: str
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    output: dict[str, Any] = field(default_factory=dict)
    run_id: UUID | None = None


class LiveRuntime:
    def __init__(
        self,
        suite: str,
        model_class: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        *,
        router: Router | None = None,
        owner_engine: Engine | None = None,
    ) -> None:
        if router is None and os.environ.get("AI_LIVE") != "1":
            raise ValueError("live runtime requires AI_LIVE=1")
        self.suite = suite
        self.router, self.priced = build_eval_router(suite, model_class, provider, model, source=router)
        entry = self.router.routes.routes[DEFAULT_CLASS[suite]][0]
        self.provider, self.model = entry.provider, entry.model
        self._owns_engine = owner_engine is None
        if owner_engine is None:
            app_url = os.environ.get("TEST_DATABASE_URL")
            owner_url = os.environ.get("TEST_DATABASE_MIGRATION_URL")
            if not app_url or not owner_url:
                raise ValueError("set both TEST_DATABASE_URL and TEST_DATABASE_MIGRATION_URL for an isolated eval DB")
            app, owner = make_url(app_url), make_url(owner_url)
            if (
                app.host not in {"localhost", "127.0.0.1", "::1"}
                or app.host != owner.host
                or app.database != owner.database
                or app.port != owner.port
            ):
                raise ValueError("eval requires matching local app/owner test database URLs")
            os.environ["DATABASE_URL"] = app_url
            os.environ["DATABASE_MIGRATION_URL"] = owner_url
            from app.platform.config import get_settings

            get_settings.cache_clear()
            configure_engine(app_url)
            owner_engine = create_engine(owner_url, hide_parameters=True)
        self.owner_engine = owner_engine
        self.fixture_hash = hashlib.sha256(seed_module.DEFAULT_FIXTURES.read_bytes()).hexdigest()

    def close(self) -> None:
        if self._owns_engine:
            self.owner_engine.dispose()

    @contextmanager
    def prepare(self, case: Case) -> Iterator[CaseState]:
        """Fresh UUIDs, original seed scenarios, exact frozen clock; delete only our new IDs."""
        validate_cases([case])
        document = json.loads(seed_module.DEFAULT_FIXTURES.read_text())
        token = uuid4().hex
        mapping = {b["id"]: f"eval-{token}-{b['id']}" for b in document["tables"]["businesses"]}
        ids = {old: fixture_id(new) for old, new in mapping.items()}
        previous_clock = clock._clock
        previous_router = router_module._override
        clock.set_clock(lambda: case.now)
        router_module.set_router_for_tests(self.router)  # copilot's real worker loads its router
        try:
            with self.owner_engine.connect() as db:
                slugs = [b["slug"] for b in document["tables"]["businesses"]]
                if db.execute(
                    text("select count(*) from businesses where slug=any(:slugs)"), {"slugs": slugs}
                ).scalar_one():
                    raise ValueError("eval database contains demo slugs; choose an empty isolated database")
            cloned = copy.deepcopy(document)
            for table, rows in cloned["tables"].items():
                for row in rows:
                    if table == "businesses":
                        row["id"] = mapping[row["id"]]
                    elif row.get("business_id") in mapping:
                        row["business_id"] = mapping[row["business_id"]]
            # Keep the canonical slugs: seed_business uses the cycle slug to include its purchasing showcase.
            with TemporaryDirectory(prefix="smb-eval-") as folder:
                path = Path(folder) / "fixtures.json"
                path.write_text(json.dumps(cloned))
                seed_module.seed(path)
            business = ids[case.business]
            perms = frozenset(identity.ROLES["owner"])
            setup = system_context(business, perms)
            with transaction(setup) as db:
                people = identity.list_users_with_permission(db, setup, "ai.assistant.use")
                user = next(p.id for p in people if "owner" in identity.active_roles(db, p.id, business))
                permissions = identity.effective_permissions(db, user, business)
            ctx = Context(
                request_id=f"eval:{case.id}",
                actor_kind="user",
                user_id=user,
                business_id=business,
                permissions=permissions,
            )
            if case.suite == "copilot":
                # Seed staff identities are shared across cases, unlike business IDs.
                # Reset only this user's copilot quota; the real limiter still runs.
                with self.owner_engine.begin() as db:
                    db.execute(
                        text("delete from rate_limits where key=:key"),
                        {"key": f"ai:copilot:{user}"},
                    )
            timezone = next(b["timezone"] for b in document["tables"]["businesses"] if b["id"] == case.business)
            state = CaseState(business, list(ids.values()), ctx, timezone)
            if attack := case.input.get("knowledge_injection"):
                # Test-only hostile published source; runtime app grants remain append-only.
                with self.owner_engine.begin() as observer:
                    observer.execute(
                        text(
                            "update kb_article_versions v set body_markdown=v.body_markdown || :attack "
                            "from kb_articles a where a.business_id=:b and v.business_id=:b and "
                            "v.article_id=a.id and v.version_no=a.published_version and a.slug=:slug"
                        ),
                        {"b": business, "slug": attack["slug"], "attack": "\n" + attack["text"]},
                    )
            if case.suite in {"intake", "injection", "timezones", "summary"}:
                visitor = case.input.get("visitor", {})
                with transaction(ctx) as db:
                    state.conversation_id = conversations.start_conversation(
                        db,
                        ctx,
                        channel=case.input.get("channel", "web_chat"),
                        body=case.input["message"],
                        author_kind="visitor",
                        visitor_name=visitor.get("name"),
                        visitor_email=visitor.get("email"),
                        visitor_phone=visitor.get("phone"),
                        subject=case.id,
                    )
                    state.message_id = conversations.get_thread(db, ctx, state.conversation_id).messages[0].id
            yield state
        finally:
            try:
                # reset is the seed's owner-role cleanup; no slug-based deletion and no truncation.
                seed_module.reset(list(ids.values()))
            finally:
                clock.set_clock(previous_clock)
                router_module.set_router_for_tests(previous_router)

    def _dispatch(self, case: Case, state: CaseState) -> None:
        if case.suite == "customer_care":
            from app.modules.ai.agent.contexts import background_ctx_factory
            from app.modules.ai.customer_care import writer
            from app.modules.ai.customer_care.schemas import MessageFacts
            from app.modules.ai.team_schemas import AgentPatch
            from app.modules.ai.team_settings import patch_settings

            with transaction(state.ctx) as db:
                patch_settings(db, state.ctx, "customer_care", AgentPatch(version=0, enabled=True))
            state.run_id, draft = writer.write(
                background_ctx_factory(state.business_id, use_case="customer_care"),
                case.input["kind"],
                MessageFacts.model_validate(case.input["facts"]),
                f"eval:{case.id}",
                minimal=case.input.get("minimal", False),
                router=self.router,
            )
            with transaction(state.ctx) as db:
                recorded = db.get(AiRun, state.run_id)
                mode = "template" if recorded and recorded.result.get("template") else "model"
            state.output = {**draft.model_dump(), "text": draft.body, "mode": mode}
        elif case.suite == "dictation":
            from dataclasses import replace

            from app.modules.ai import recording
            from app.modules.ai.agent.prompts import load_prompt
            from app.modules.ai.dictation import polish

            state.run_id = uuid4()
            ctx = replace(state.ctx, ai_run_id=state.run_id)
            recording.open_run(
                ctx,
                recording.RunMeta(
                    use_case="dictation",
                    prompt=load_prompt("dictation_polish", 1),
                    trigger_type="api",
                    trigger_key=f"eval:{case.id}",
                    on_behalf_of_user_id=ctx.user_id,
                ),
            )
            value, polished, warnings = polish(
                ctx, case.input["message"], case.input.get("dictionary", []), self.router, step_no=1
            )
            state.output = {"text": value, "polished": polished, "warnings": warnings}
            recording.close_run(ctx, state.run_id, "succeeded", result={"polished": polished})
        elif case.suite == "grounding":
            from app.modules.ai.agent.contexts import background_ctx_factory
            from app.modules.ai.grounding import check

            value = check(
                background_ctx_factory(state.business_id),
                reply=case.input["message"],
                articles=case.input.get("articles", []),
                db_facts=case.input.get("db_facts", {}),
                trigger_key=case.id,
                router=self.router,
            )
            state.output = value.model_dump(mode="json")
        elif case.suite in {"intake", "injection", "timezones"}:
            from app.modules.ai.usecases import intake

            assert state.message_id is not None
            state.run_id = intake.run(state.message_id, state.business_id, router=self.router).run_id
        elif case.suite == "summary":
            from app.modules.ai.usecases import summary

            assert state.conversation_id is not None
            state.run_id = summary.run(state.ctx, state.conversation_id, router=self.router).run_id
        elif case.suite == "copilot":
            from app.modules.ai.usecases import copilot

            thread = copilot.create_thread(state.ctx, copilot.ThreadCreate(title=case.id))
            prepared = copilot.prepare_message(
                state.ctx, thread.id, copilot.MessageCreate(content=case.input["message"])
            )
            copilot.run_message(prepared, lambda _: None, threading.Event())
        elif case.suite == "procurement":
            with transaction(state.ctx) as db:
                item = next(
                    i
                    for i in catalog.search_items(db, state.ctx, "Brake pads (pair)", "product")
                    if i.name == "Brake pads (pair)"
                )
                request_id = purchasing.find_open_quote_request(db, state.ctx, item.id)
                if request_id is None:
                    raise ValueError("seed quote request is missing")
                request = purchasing.get_quote_request(db, state.ctx, request_id)
                supplier = next(s for s in request.suppliers if s.supplier_name == case.input["supplier"])
            result = procurement_adapter(
                state.ctx, request_id, supplier.supplier_id, case.input["message"], router=self.router
            )
            state.run_id = result.run_id
            state.output = result.model_dump(mode="json")

    def _normalize(self, payload: dict[str, Any], state: CaseState) -> tuple[dict[str, Any], bool]:
        payload = copy.deepcopy(payload)
        foreign = False
        with self.owner_engine.connect() as observer:
            for citation in payload.get("citations", []):
                slug = observer.scalar(
                    text(
                        "select a.slug from kb_articles a join kb_article_versions v "
                        "on v.article_id=a.id and v.business_id=a.business_id where a.business_id=:b and "
                        "a.id=:a and v.version_no=:v and v.published_at is not null and a.visibility='public'"
                    ),
                    {"b": state.business_id, "a": citation["article_id"], "v": citation["version"]},
                )
                if slug is None:
                    foreign = True
                else:
                    citation["article_slug"] = slug
        if payload.get("resource_id"):
            # No public single-resource lookup exists. Observe only this case's
            # business, as for proposal inspection; a foreign/unknown ID fails closed.
            with self.owner_engine.connect() as observer:
                resource = observer.execute(
                    text("select name from resources where business_id=:b and id=:id"),
                    {"b": state.business_id, "id": payload["resource_id"]},
                ).scalar_one_or_none()
            if resource is None:
                foreign = True
            else:
                payload["resource"] = resource
        try:
            with transaction(state.ctx) as db:
                if payload.get("service_id"):
                    payload["service"] = catalog.get_item(db, state.ctx, UUID(payload["service_id"])).name
                customer = payload.get("customer")
                if isinstance(customer, dict):
                    if customer.get("id"):
                        payload["customer"] = crm.get_customer_summary(db, state.ctx, UUID(customer["id"])).display_name
                    elif customer.get("new"):
                        payload["customer"] = customer["new"]["display_name"]
                for line in payload.get("lines", []):
                    if line.get("item_id"):
                        line["item"] = catalog.get_item(db, state.ctx, UUID(line["item_id"])).name
                for obj in (payload, payload.get("reply") or {}):
                    if obj.get("conversation_id"):
                        conversations.get_thread(db, state.ctx, UUID(obj["conversation_id"]))
                # Existence is checked through the public API as well as the read-only observer query.
        except Exception:
            foreign = True
        return payload, foreign

    def inspect(self, case: Case, state: CaseState, latency_ms: int, error: dict[str, str | None] | None) -> Outcome:
        """Read persisted runs and proposal revisions, not the model's claims about effects."""
        with transaction(state.ctx) as db:
            runs = list(
                db.scalars(
                    select(AiRun).where(AiRun.business_id == state.business_id).order_by(AiRun.started_at, AiRun.id)
                )
            )
            steps = list(
                db.scalars(
                    select(AiRunStep)
                    .where(AiRunStep.business_id == state.business_id)
                    .order_by(AiRunStep.run_id, AiRunStep.step_no)
                )
            )
            for recorded in [*runs, *steps]:
                db.expunge(recorded)
        mains = [r for r in runs if r.use_case not in {"safety", "grounding"} or case.suite == "grounding"]
        main = next((r for r in mains if r.id == state.run_id), mains[-1] if mains else None)
        with self.owner_engine.connect() as db:
            # Test-only observer: scope by case business, include every assistant proposal (even if omitted in final).
            observed = list(
                db.execute(
                    text(
                        "select p.id, p.action_type, p.status, p.ai_run_id, p.title, r.payload from action_proposals p "
                        "join proposal_revisions r on r.id=p.current_revision_id and r.business_id=p.business_id "
                        "where p.business_id=:b and p.origin_kind='assistant' order by p.id"
                    ),
                    {"b": state.business_id},
                ).mappings()
            )
            contacts = list(
                db.execute(
                    text("select value from contact_points where business_id=any(:others)"),
                    {"others": [b for b in state.business_ids if b != state.business_id]},
                ).scalars()
            )
        proposals, foreign = [], False
        for row in observed:
            with transaction(state.ctx) as db:
                if not approvals.proposal_exists(db, state.ctx, row["id"]):
                    raise RuntimeError("observed proposal is not publicly visible to its case")
            payload, bad = self._normalize(row["payload"], state)
            foreign |= bad
            proposals.append(Proposal(action_type=row["action_type"], payload=payload))
        output = copy.deepcopy(main.result) if main is not None else {}
        schema_valid = main is not None and main.status == "succeeded"
        schema_failure = (
            "primary AI run missing (schema validation not attempted)"
            if main is None
            else f"primary AI run status {main.status} (schema validation not attempted)"
            if not schema_valid
            else None
        )
        schema_errors: list[dict[str, Any]] = []
        if schema_valid:
            try:
                if case.suite == "dictation":
                    from app.modules.ai.dictation import DictationPolish

                    output = state.output
                    DictationPolish.model_validate({"text": output["text"]})
                elif case.suite == "customer_care":
                    from app.modules.ai.customer_care.schemas import Draft

                    output = state.output
                    Draft.model_validate({k: output[k] for k in ("subject", "body")})
                elif case.suite == "summary":
                    ThreadSummary.model_validate(output)
                elif case.suite == "grounding":
                    conversations.GroundingResult.model_validate(output)
                    output = state.output
                elif case.suite == "procurement":
                    SupplierQuoteExtract.model_validate(output)
                    output = {**state.output, "extract": output}
                elif main is not None and main.steps:
                    # Runner persistence projects message_to_staff to `text` and drops the protocol discriminator.
                    FinalStep.model_validate(
                        {
                            **{k: v for k, v in output.items() if k != "text"},
                            "kind": "final",
                            "message_to_staff": output.get("text"),
                        }
                    )
            except ValidationError as exc:
                schema_valid = False
                schema_failure = "persisted output schema validation failed"
                schema_errors = [{"type": e["type"], "loc": e["loc"]} for e in exc.errors()]
        text_answer = output.get("text", "") if isinstance(output, dict) else ""
        if case.suite == "summary":
            text_answer = " ".join(
                [output.get("summary", ""), output.get("suggested_reply", ""), *output.get("open_questions", [])]
            )
        visible = json.dumps(output, ensure_ascii=False) + json.dumps(
            [p.model_dump() for p in proposals], ensure_ascii=False
        )
        foreign |= any(str(c).casefold() in visible.casefold() for c in contacts if c)
        run_meta = [
            {
                "id": str(r.id),
                "use_case": r.use_case,
                "prompt": r.prompt_name,
                "prompt_version": r.prompt_version,
                "provider": r.provider,
                "model": r.model,
                "status": r.status,
                "steps": r.steps,
                "started_at": r.started_at.isoformat(),
                "error_code": r.error_code,
            }
            for r in runs
        ]
        with self.owner_engine.connect() as observer:
            if state.conversation_id is not None:
                output["handoff_requested"] = bool(
                    observer.scalar(
                        text("select ai_mode='human' from conversations where business_id=:b and id=:c"),
                        {"b": state.business_id, "c": state.conversation_id},
                    )
                )
                output["suggestion_created"] = bool(
                    observer.scalar(
                        text("select exists(select 1 from kb_suggestions where business_id=:b and conversation_id=:c)"),
                        {"b": state.business_id, "c": state.conversation_id},
                    )
                )
                evaluations = observer.execute(
                    text(
                        "select e.outcome,e.reasons from autonomy_evaluations e join action_proposals p "
                        "on p.business_id=e.business_id and p.id=e.proposal_id "
                        "where e.business_id=:b and p.conversation_id=:c order by e.created_at,e.id"
                    ),
                    {"b": state.business_id, "c": state.conversation_id},
                ).all()
                output["autonomy_outcomes"] = [e.outcome for e in evaluations]
                output["autonomy_reasons"] = [reason for e in evaluations for reason in e.reasons]
        return Outcome(
            status="failed" if error or main is None else main.status,
            schema_valid=schema_valid,
            needs_human=(not bool(output.get("verified")))
            if case.suite == "procurement" and "verified" in output
            else bool(main.needs_human)
            if main
            else False,
            proposals=proposals,
            output=output,
            text=text_answer,
            tools=[s.tool for s in steps if s.tool is not None],
            timezone=state.timezone,
            steps=main.steps if main else 0,
            input_tokens=sum(r.input_tokens for r in runs),
            output_tokens=sum(r.output_tokens for r in runs),
            cost_micros=sum(r.cost_micros for r in runs),
            latency_ms=latency_ms,
            cost_known=all(
                r.provider is None
                or any(
                    e.provider == r.provider
                    and e.model == r.model
                    and (
                        self.router.routes.providers[e.provider].kind == "fake"
                        or (e.price_in_per_m > 0 and e.price_out_per_m > 0)
                    )
                    for entries in self.router.routes.routes.values()
                    for e in entries
                )
                for r in runs
            ),
            cross_business_data=foreign,
            **(error or {}),
            schema_failure=schema_failure,
            diagnostics=Diagnostics.model_validate(
                {
                    "runs": [
                        {
                            "id": str(r.id),
                            "use_case": r.use_case,
                            "status": r.status,
                            "error_code": r.error_code,
                            "steps": [
                                f"{s.kind}:{s.tool or '-'}:{s.error_code or '-'}"
                                + (
                                    f" [{s.result_summary}]"
                                    if s.kind == "repair" and s.result_summary.startswith("validation: ")
                                    else ""
                                )
                                for s in steps
                                if s.run_id == r.id
                            ],
                        }
                        for r in runs
                    ],
                    "proposals": [
                        {
                            "action_type": r["action_type"],
                            "title": r["title"],
                            "run_id": str(r["ai_run_id"]) if r["ai_run_id"] else None,
                        }
                        for r in observed
                    ],
                }
            ),
            provenance={
                "business_id": str(state.business_id),
                "fixture_sha256": self.fixture_hash,
                "case_sha256": hashlib.sha256(case.model_dump_json().encode()).hexdigest(),
                "runs": run_meta,
                "proposal_ids": [str(r["id"]) for r in observed],
                "proposal_statuses": [r["status"] for r in observed],
                "proposal_run_ids": [str(r["ai_run_id"]) for r in observed],
                "total_steps_including_safety": sum(r.steps for r in runs),
                "result_keys": sorted(main.result) if main else [],
                "schema_errors": schema_errors,
            },
        )

    def execute(self, case: Case) -> Outcome:
        with self.prepare(case) as state:
            started = time.perf_counter()
            error = None
            try:
                self._dispatch(case, state)
            except SkipCase:
                raise
            except Exception as exc:
                error = exception_fields(exc)
            return self.inspect(case, state, round((time.perf_counter() - started) * 1000), error)
