# Integration map: existing code the AI layer calls

**Verified against the code on 2026-11-03, and again on 2026-11-03 after the PRE cards** (signatures dumped from the
running registry). Items marked **(PR-01)**, **(PR-02)** or **(PQ-01)** were built by those cards on 2026-11-03 and match
this map exactly. If anything here
does not match the code, stop and report it (README stop rules); re-generate with the command in §H.

Import rule: the `ai` module imports other modules **only** as `from app.modules.<m> import public as <m>`.

## §A Platform helpers

```python
from app.platform.context import Context, assistant_context, system_context
assistant_context(business_id: UUID, permissions: frozenset[str], *, on_behalf_of_user_id: UUID | None,
                  ai_run_id: UUID) -> Context          # actor_kind="assistant"; ctx.ai_run_id is set
system_context(business_id: UUID | None, permissions: frozenset[str] = frozenset()) -> Context
from app.platform.db import transaction                # with transaction(ctx) as db: ...  (commits on exit, sets RLS)
from app.platform import jobs, events, audit, clock
jobs.enqueue(db, kind: str, payload: dict | None = None, *, business_id: UUID | None = None, run_at=None,
             priority: int = 100, dedupe_key: str | None = None, max_attempts: int = 8) -> UUID   # dedupe → no-op
events.publish(db, ctx, event: DomainEventPayload) -> UUID
from app.platform.http import client                   # httpx client with the egress allowlist; never use httpx directly
client(local=True)                                     # (PR-02) plain HTTP to LOCAL_INFERENCE_HOSTS only (llama.cpp)
from app.platform.errors import DomainError, NotFound, Conflict, Forbidden, Invalid, RateLimited
from app.platform.ratelimit import hit                 # hit(db, key, limit=..., window_seconds=...)
```
Registration (`ai/registration.py`, `register(reg: Registry)`):
`reg.add_permissions(set)`, `reg.add_router(router, "/ai", ["ai"])`,
`reg.add_job(kind, handler)` with `handler(db: Session, ctx: Context, payload: dict) -> None` (runs inside a transaction),
**(PR-02)** `reg.add_job(kind, handler, transactional=False)` with `handler(ctx: Context, payload: dict) -> None` (no open
transaction; job success/failure is recorded separately) — **all AI job handlers use this form**,
`reg.subscribe(event_type, subscriber_name, handler)` with `handler(db, ctx, payload: dict, event_id: UUID) -> None`,
`reg.add_cron(name, job_kind, every_seconds)` (one global job, no business), **(PR-02)**
`reg.add_cron(name, job_kind, every_seconds, per_business=True)` (one job per active business, `ctx.business_id` set), `reg.add_export_handler(module, fn)` (export `ai_runs`, `ai_run_steps`,
`assistant_*`; erasure: AI tables hold no customer free text beyond `result`, which the erasure handler scrubs —
follow `marketing/lifecycle.py`).

## §B Read functions wrapped by read tools

| Tool | Calls (exact) | Returns (fields the tool passes on) |
|---|---|---|
| `get_conversation` | `conversations.get_thread(db, ctx, conversation_id, last_n=20)` | `ThreadDto`: conversation_id, channel, status, customer_id, visitor_name, messages |
| `find_customers` | `crm.find_customer_candidates(db, ctx, *, name=None, email=None, phone=None, limit=5)` | `CustomerCandidate`: customer_id, display_name, score (0–1), reason |
| `get_customer` | `crm.get_customer_summary(db, ctx, customer_id)` | `CustomerSummary`: id, display_name, kind, status, contact_types, tags, timezone, last_interaction_at |
| `customer_history` | `crm.get_timeline(db, ctx, customer_id, limit=50)` | `TimelineEntryOut` list |
| `list_services` | `catalog.search_items(db, ctx, query, "service", limit=20)` | `CatalogItemOut`: id, name, duration_minutes, price_minor, currency, is_bookable_online |
| `get_service_parts` | `catalog.get_service_requirements(db, ctx, service_id)` | `ServiceRequirements`: service_id, name, duration_minutes, buffer_minutes, resource_kind, price_minor, currency, parts (item ids and quantities) |
| `find_slots` | `scheduling.find_slots(db, ctx, SlotQuery(service_id, earliest, latest, resource_id=None, limit=10))` | `SlotOut`: starts_at, ends_at, resource_id, resource_name, local_label |
| `list_appointments` | `scheduling.list_appointments(db, ctx, *, start, end, resource_id=None, customer_id=None)` | `AppointmentOut` list |
| `check_stock` | `inventory.get_availability(db, ctx, item_ids)` | `AvailabilityOut`: item_id, item_name, unit, on_hand, reserved, available, reorder_point, below_reorder |
| `list_low_stock` | `inventory.list_below_reorder(db, ctx)` | `ReorderSuggestion`: item_id, item_name, available, reorder_point, target_level, suggested, explanation |
| `today_summary` **(PR-01)** | `dashboard.today_summary(db, ctx) -> TodaySummary` | agenda count and next 5, waiting conversations, pending approvals, low-stock items, delivery issues |
| `list_overdue_invoices` **(PR-01)** | `sales.list_overdue_invoices(db, ctx, *, as_of: date | None = None, limit: int = 50) -> list[OverdueInvoice]` | id, number, customer_id, bill_to_name, due_date, days_overdue, total_minor, currency, version, last_reminder_at (the DTO also has `bill_to_email`; the **tool drops it** — addresses stay server-side) |
| Payment follow-up point read | `sales.get_overdue_invoice(db, ctx, invoice_id, *, lock=False, as_of=None) -> OverdueInvoice | None` | Same fields as the overdue DTO; indexed business-scoped lookup. With `lock=True`, cooldown, pending proposals and the expected draft version are checked under the invoice lock in the proposal transaction. |
| Payment follow-up scan page | `sales.overdue_invoice_page(db, ctx, *, after=None, limit=500) -> list[OverdueInvoice]` | Business-local overdue DTOs in bounded keyset pages ordered by `(due_date, id)`; used only to enqueue one deduplicated non-transactional job per invoice, without inference. |
| `list_suppliers_for_item` **(PQ-01)** | `purchasing.list_suppliers_for_item(db, ctx, item_id) -> list[SupplierOption]` | supplier_id, supplier_name, has_email, last_price_minor, currency, lead_time_days, on_time_rate (0–1 or None), orders_count |
| `get_quote_request` **(PQ-01)** | `purchasing.get_quote_request(db, ctx, quote_request_id) -> QuoteRequestOut` | lines, needed_by, invited suppliers with status, received quotes |
| `compare_quotes` **(PQ-01)** | `purchasing.compare_quotes(db, ctx, quote_request_id) -> QuoteComparison` | ranked quotes with score and score components (deterministic) |
| `list_late_purchase_orders` **(PQ-01)** | `purchasing.list_late_purchase_orders(db, ctx, *, as_of: date | None = None) -> list[LatePO]` | po id, number, supplier, expected_delivery_date, days_late, open lines |

**Inputs and guards (PR-02):**

| Use | Function (exact) | Returns |
|---|---|---|
| Intake input | `conversations.get_message_context(db, ctx, message_id) -> MessageContext` | conversation_id, channel, author_kind, body, customer_id, visitor_name, visitor_email, visitor_phone, created_at (contact fields go to `RunState.server_facts`, never into the prompt except the visitor's first name) |
| Duplicate guard | `approvals.find_open_proposals(db, ctx, *, conversation_id: UUID | None = None, action_types: list[str] | None = None, item_id: UUID | None = None, invoice_id: UUID | None = None) -> list[OpenProposal]` | id, action_type, title, origin_kind, created_at (status `pending_review` or `approved`); filters combine using JSONB containment on the current revision's `payload.lines[].item_id` or top-level `payload.invoice_id` (string UUID); historical/nested invoice references do not match |
| Campaign segment metadata (U5) | `marketing.get_segment(db, ctx, segment_id: UUID) -> SegmentBrief` | name, filter_description (configured filters only; no audience evaluation or customer data); requires campaign read or write permission |
| Procurement scan | **(PQ-01)** `purchasing.list_quote_requests_due(db, ctx, *, as_of: date | None = None) -> list[QuoteRequestDue]` | requests with status `sent` whose reply-by has passed or whose invites are all answered, and no PO yet |
| Procurement duplicate guard | **(AI-12)** `purchasing.find_open_quote_request(db, ctx, item_id) -> UUID | None` | first sent request containing the tenant's item; validates item ownership; drafts/closed/cancelled do not block |
| Procurement chase | **(PQ-01)** `purchasing.list_unanswered_invites(db, ctx, *, as_of: date | None = None) -> list[UnansweredInvite]` | invite id, quote_request_id, supplier_id, supplier_name, reply_by, days_overdue, last_followup_at |
| Procurement target job/tool | **(Review A)** `purchasing.get_late_purchase_order(db, ctx, purchase_order_id, *, as_of: date | None = None) -> LatePO | None` | scoped point read; same late/open eligibility as the list; requires purchasing read |
| Procurement invite job/tool | **(Review A)** `purchasing.get_unanswered_invite(db, ctx, quote_request_id, supplier_id, *, as_of: date | None = None) -> UnansweredInvite | None` | scoped point read; same overdue/live eligibility as the list; requires purchasing read |

The hourly procurement scan enqueues `ai.procurement_followup` once per business/target/hour (payload: either
`purchase_order_id` or `quote_request_id` + `supplier_id`). Its non-transactional handler reloads only that target,
then runs one bounded follow-up completion with the existing rolling three-day proposal guard. A target failure is
retried independently by the job worker; the scan performs no inference.

Context note for intake: the inbound message text is `get_message_context(...).body`; fence it as untrusted. Business
facts: `org.get_business_brief(db, ctx) -> BusinessBrief` (id, slug, name, timezone, currency, locale) and
`org.get_opening_hours(db, ctx) -> list[OpeningHoursItem]`. Current time: `app.platform.clock.now()` (UTC; convert with
`zoneinfo.ZoneInfo(brief.timezone)`), never `datetime.now()`.

## §C Proposals

```python
from app.modules.approvals import public as approvals
approvals.create_proposal(db, ctx, *, action_type: str, payload: dict, title: str,
                          evidence: list[Evidence] | None = None, expires_in: timedelta | None = None,
                          conversation_id: UUID | None = None) -> ProposalRef      # .id, .status, .current_revision_id
from app.modules.approvals.public import Evidence     # kind: "message"|"tool_result"|"record"|"note",
                                                      # ref_type: str|None, ref_id: UUID|None, excerpt: str (≤ 500)
```
With an assistant context the permission checked is **`ai.assistant.use`**; the payload is validated by the executor's
model at creation; a person approves; execution re-validates. The proposal's origin is recorded as `assistant`.

## §D Executor payloads used by propose tools (exact, from the registry)

| Tool | `action_type` | Payload (`?` = optional) | Approve permission |
|---|---|---|---|
| `propose_booking` | `scheduling.book_appointment` | `customer: {"id": UUID} \| {"new": CustomerCreate}`, `service_id`, `resource_id`, `starts_at` (ISO UTC), `hold_id?`, `notes?` (≤ 2000), `reply?: {conversation_id, body (≤ 4000)}` | scheduling.proposal.approve |
| `propose_reply` | `conversations.send_reply` | `conversation_id`, `body` (1–4000) | conversations.proposal.approve |
| `propose_customer` | `crm.create_customer` | `CustomerCreate`: `display_name`, `kind?`, `given_name?`, `family_name?`, `contact_points?: [{type: "email"\|"phone", value, is_primary?}]`, `consents?` (never set by the AI), `tags?` | crm.proposal.approve |
| `propose_reorder` | `inventory.create_reorder_draft` | `lines: [{item_id, quantity (Decimal > 0), reason_text? ≤ 300}]` (1–100), `note?` ≤ 1000 — the tool takes `item_ids` and fills `quantity` from `list_below_reorder().suggested` | inventory.proposal.approve |
| (U5, not a proposal) | `marketing.add_revision(db, ctx, campaign_id, subject, body_markdown, *, created_by_kind="assistant", ai_run_id=run_id)` | — | — |
| `propose_quote_request` **(PQ-01)** | `purchasing.send_quote_requests` | `lines: [{item_id, quantity}]`, `supplier_ids` (1–5), `needed_by: date`, `reply_by: date`, `message?` ≤ 2000 | purchasing.po.approve |
| `propose_supplier_quote` **(PQ-01)** | `purchasing.record_supplier_quote` | `quote_request_id`, `supplier_id`, `lines: [{item_id, unit_price_minor, quantity_available?}]`, `currency`, `lead_time_days`, `min_quantity?`, `valid_until?`, `notes?`, `source: "assistant_extracted"` | purchasing.po.write |
| `propose_po_from_quote` **(PQ-01)** | `purchasing.create_po_from_quote` | `quote_id`, `quote_version` | purchasing.po.approve |
| `propose_supplier_followup` **(PQ-01)** | `purchasing.send_supplier_followup` | `supplier_id`, `about: {"purchase_order_id"} \| {"quote_request_id"}`, `body` ≤ 2000 | purchasing.po.write |
| `propose_payment_reminder` **(PR-01)** | `sales.send_payment_reminder` | `invoice_id`, `invoice_version`, `body` ≤ 2000 | sales.document.write |

Other executors that exist (not used by tools in the MVP): `crm.update_customer`, `inventory.adjust_stock`,
`marketing.approve_campaign`, `purchasing.approve_po`, `purchasing.create_po_from_draft`, `sales.confirm_order`,
`sales.create_quote`, `scheduling.cancel_appointment`, `scheduling.reschedule_appointment`.

## §E Events and existing jobs

| Event type | Payload fields (exact) | Published when |
|---|---|---|
| `conversations.message_received` | conversation_id, message_id, channel, author_kind, is_first_message | any message added; **intake only for author_kind in {"visitor","customer"}** |
| `inventory.stock_below_reorder_level` | item_id, stock_location_id, available, reorder_point | available crosses below the reorder point |
| `approvals.proposal_created` | proposal_id, action_type, origin_kind, title | proposal created |
| `approvals.proposal_decided` | proposal_id, decision (`approve`\|`reject`) | a person decided |
| `approvals.proposal_closed` | proposal_id, action_type, outcome (`succeeded`\|`failed`\|`rejected`\|`expired`\|`cancelled`) | proposal finished; scheduling releases holds bound to it unless `succeeded` |
| `purchasing.quote_received` **(PQ-01)** | quote_request_id, supplier_id, declined (bool), all_answered (bool) | a supplier answered (form, staff entry or confirmed extraction) |

Existing crons run every 60 s (`approvals-expire`, `notifications-reap-sending`, `scheduling-release-holds`); the worker
claims jobs with `jobs.claim`. Event handlers run inside the worker's transaction: **they only enqueue jobs**, they never
call a model.

## §F Writes the AI layer may do itself (not through proposals)

| Write | Function | Who |
|---|---|---|
| Scheduling hold (reversible, 30 min) | `scheduling.place_hold(db, ctx, *, service_id, resource_id, starts_at, customer_id, proposal_id=None) -> HoldDto` (hold_id, starts_at, ends_at, hold_expires_at); `customer_id` must be an existing customer | `hold_slot` tool |
| Bind a hold to its proposal | **(PR-02)** `scheduling.bind_hold(db, ctx, hold_id, proposal_id) -> None` (same business, status `held`, unbound; idempotent for the same proposal) | `propose_booking`, same transaction as the proposal |
| Internal note on a conversation | `conversations.add_internal_note(db, ctx, conversation_id, body, author_kind="assistant")` | intake, after the run |
| Handoff to staff | `conversations.request_handoff(db, ctx, conversation_id, reason)` | intake safety path |
| Campaign revision (draft only) | `marketing.add_revision(...)` (§D) | U5 |
| Its own tables | `ai_*`, `assistant_*` | ai module |

## §G Test fixtures you can use (pytest plugin `app.platform.testing.plugin`, auto-loaded)

`owner_engine` (superuser engine for asserts), `business` / `other_business` / `make_business(slug, timezone)` (fresh
businesses), `freeze_time` (set the app clock), tables are truncated between tests. Helpers:
```python
from app.platform.testing.auth import ctx_for, client_for
ctx_for(business_id, permissions=(), *, user_id=None, actor="user") -> Context
client_for(app, registry, business_id, permissions, *, csrf=True) -> TestClient   # authenticated API client
from app.seed import seed; from app.platform.ids import fixture_id   # seed() loads data/fixtures.json;
# businesses: fixture_id("biz-cycle") (Example Cycle Works, America/Denver), fixture_id("biz-garden")
```
Seed facts used by evals: customers Ada, Ben, Chloe, Dana, Dan (Example Cycle Works); services "Brake service",
"Standard tune-up", "Flat tyre repair"; part "Brake pads (pair)" needed by brake service; demo Tuesday 10 Nov 2026.
Procurement and payment showcase (all dates relative to the seed day S; details and scores in `data/README.md`): suppliers
Example Parts Supply, Example Brake Direct and Example Cycle Wholesale all sell "Brake pads (pair)" (price, lead time,
on-time rate: $12.00, 3 days, none yet; $14.00, 2 days, 1.0; $10.50, 16 days, 0.333), with one open quote request for
10 pairs sent to them (Brake Direct and Cycle Wholesale answered, Parts Supply pending; the cheapest does not rank first).
One late purchase order (Cable set x 5 from Example Cycle Wholesale, sent, expected S-4) and one overdue invoice (Example
Courier Co., fleet@example.com, $190.00, due S-10) come with it.
Test database per agent: card PR-00 (`TEST_DATABASE_URL`, `TEST_DATABASE_MIGRATION_URL`).

## §H Re-generate this map

```bash
cd backend && .venv/bin/python - <<'EOF'
import sys, inspect, importlib; sys.path.insert(0, ".")
from app.main import build_registry; reg = build_registry()
for k, s in sorted(reg.executors.items()):
    print(k, s.approve_permission, list(s.payload_model.model_json_schema().get("properties", {})))
for m in ["conversations","crm","catalog","scheduling","inventory","org","sales","purchasing","dashboard","approvals"]:
    try: mod = importlib.import_module(f"app.modules.{m}.public")
    except ImportError: continue
    for n, f in inspect.getmembers(mod, inspect.isfunction):
        if f.__module__.startswith("app.modules"): print(f"{m}.{n}{inspect.signature(f)}")
EOF
```

### Second-review batch B public additions (2026-11-04 UTC)

| Need | Public function | Caller |
|---|---|---|
| Outcome marker existence without reading history | `conversations.has_internal_note_marker(db, ctx, conversation_id, marker) -> bool` (thread-read permission; business/thread index; suffix escaped) | Intake outcome note |
| Complete active service context | `catalog.list_active_service_requirements(db, ctx) -> list[ServiceRequirements]` (catalog-read permission; durations, prices and parts; active only) | Intake trusted facts |
| Refuse expired hold binding | `scheduling.bind_hold(db, ctx, hold_id, proposal_id)` also checks expiry under the hold row lock | Booking proposal |
