# AI layer architecture

Read this when your card lists it. Interfaces named here are defined exactly in [`contracts.md`](contracts.md); the
existing ERP functions are listed in [`integration-map.md`](integration-map.md).

## 1. Principles (non-negotiable)

1. **The model proposes; code decides.** The AI reads through tools and creates *proposals* in the existing approvals
   engine. A person approves; the existing *executor* performs the change. The only direct write a tool may do is a
   reversible scheduling hold.
2. **Numbers come from code.** Prices, totals, quantities, availability, rankings and dates are computed by existing
   deterministic functions. The model may choose among them and explain them, never invent or recalculate them.
3. **The model never chooses the tenant or its permissions.** Tools receive `ctx` from the server; no tool argument is a
   business id; permissions are checked by the tool, not trusted from the model.
4. **Untrusted text is data.** Customer messages, supplier replies and notes are fenced (`<untrusted>`); instructions
   inside them have no authority.
5. **Every model output is validated** against a JSON schema (Pydantic). One repair attempt, then the run fails safely.
6. **Provider-agnostic.** Use cases ask for a *model class*; routing picks a provider and model from configuration and
   the business's policy. No use case names a provider.
7. **Bounded and recorded.** Step, token, time and cost limits per run; a daily token budget per business; every run and
   step recorded without prompt text.
8. **No DB transaction is held across a network call.** Model calls happen outside transactions; each tool call runs in
   its own short transaction.

## 2. Components

```
                ┌──────────────────────────── module `ai` ─────────────────────────────┐
 triggers       │  usecases/  intake · copilot · summary · reorder_note · briefing ·      │
 ─────────      │             procurement · campaign_draft · payment_followup             │
 event subscr.──►      │                          │                                       │
 jobs (cron)  ──►      ▼                          ▼                                       │
 API (+SSE)   ──►  agent/runner.py (multi-step)   structured.py (single-shot)             │
                │      │        │                         │                               │
                │      │        └─► agent/tools/* ──► other modules' public.py (reads)    │
                │      │                          └─► approvals.public.create_proposal    │
                │      ▼                                   (propose tools)                │
                │  gateway/router.py ──► gateway/openai_compat.py ──► Vultr Serverless    │
                │   (policy, fallback)   gateway/anthropic.py     ──► external (optional) │
                │      │                 gateway/fake.py (tests)                          │
                │  usage.py (budgets) · recording.py (ai_runs, ai_run_steps)              │
                └─────────────────────────────────────────────────────────────────────────┘
 After approval (existing code, unchanged): approvals engine ──► executor of the action type ──► outbox / records
```

| Layer | Responsibility | Never does |
|---|---|---|
| Gateway adapters | HTTP to one provider; JSON mode; parse usage; map errors to `ProviderError` kinds | Choose models, retry across providers, know about businesses |
| Router | Pick `(provider, model)` for a model class under the business policy; fall back on transient errors | Call tools, record runs |
| Runner | The multi-step loop: build messages, call router, validate `AgentStep`, run tools, enforce limits, record | Contain use-case logic |
| Structured completion | One call → one validated object (summaries, drafts, extraction) with the same limits and recording | Call tools |
| Tools | Thin, typed wrappers around `public.py` functions and `create_proposal` | Write records directly (except hold), accept business ids |
| Use cases | Choose prompt, model class, allowed tools, context; interpret the final result | Talk HTTP or SQL directly |
| Triggers | Subscribers, jobs, API endpoints that start use cases | Run model calls inside a request transaction |

## 3. Run lifecycle

```
create run (status=running) ──► [budget check] ──► step loop ──► finish
                                    │ over budget → budget_exceeded
                                    │ policy forbids every route → policy_blocked
       step loop: router.complete → validate AgentStep ─invalid→ one repair ─invalid→ failed(ai.invalid_model_output)
                  tool_call → tool allowed? permission? args valid? → run tool → observation → next step
                  final → succeeded (result may say needs_human) ; steps exhausted → step_limit
                  provider errors after fallback → failed(ai.provider_unavailable)
```

- **Statuses** (frozen list in `contracts.md`): `running`, `succeeded`, `failed`, `step_limit`, `budget_exceeded`,
  `policy_blocked`. "Needs a human" is a *successful* run whose result has `needs_human=true`.
- A run that ends in any non-success status creates **no further side effects** (proposals already created stay; they
  still need approval, and a person can reject them).
- Tool errors are **observations**, not crashes: the model sees `{"error": code, "detail": ...}` and may try again.
- **Recording:** one `ai_runs` row per run (provider, model, tokens, cost, latency, status); one `ai_run_steps` row per
  step with redacted arguments and a ≤ 500-character result summary. Prompt and completion text are never stored;
  the few stored texts are listed in contracts §2 "Storage rules".
- **Retries are safe:** every hold and proposal a tool creates is recorded in `ai_effects` in the same transaction; a
  retried job rebuilds its state from it and never creates the same effect twice.
- **Where the loop runs:** AI jobs are registered *non-transactional* (PR-02), so no database transaction is open while
  the model is called; event subscribers only enqueue jobs; scans run once per business (per-business crons).

## 4. Who acts: contexts

| Trigger | Context | Permissions | Proposals are attributed to |
|---|---|---|---|
| Background (intake, reorder note, procurement scans, payment follow-up) | `assistant_context(business, AI_SYSTEM_PERMISSIONS, on_behalf_of_user_id=None, ai_run_id=run.id)` | the fixed read set + `ai.assistant.use` | the assistant |
| Staff request (copilot, summary, campaign draft) | `assistant_context(business, user_perms ∩ AI_TOOL_PERMISSIONS ∪ {ai.assistant.use}, on_behalf_of_user_id=user, ...)` | never more than the user has | the assistant on behalf of the user |

Separation of duties: a proposal created on behalf of a user cannot be approved by that same user unless the business
allows self-approval (PR-02 extends the existing staff rule to assistant proposals). Staff-triggered runs never get
more permissions than the user has (contracts §3 formula).

## 5. Main sequences

**U1 Intake** (background, per inbound message):
`conversations.message_received` → subscriber enqueues job `ai.intake` with `dedupe_key="intake:{message_id}"` →
job runs `usecases.intake.run(message_id)` → duplicate guard → safety screen (public chat only) → **customer matching in
code** (ambiguous → note and stop) → runner with intake prompt and tools (`get_conversation`, `get_customer`,
`list_services`, `get_service_parts`, `find_slots`, `check_stock`, `hold_slot`, `propose_booking`, `propose_reply`) →
exactly one internal note summarising what was proposed or why a person is needed. Existing customer: hold, then booking
proposal bound to the hold. New customer: booking proposal carrying the new customer (built by the tool from
server-side visitor fields), no hold. At most one proposal per message.

**U2 Copilot** (staff, streaming): `POST /ai/copilot/threads/{id}/messages` → endpoint creates the run, then streams
Server-Sent Events from the runner's step callback (`step`, `proposal`, `final`, `error`) → messages stored in
`assistant_messages`. The HTTP request holds **no** DB transaction while streaming.

**Single-shot** (U3 summary, U4 reorder note, U6 briefing — cached per user, U5 campaign draft, U10 payment reminder): use case gathers
facts with `public.py` reads → `structured.complete(model_class, prompt, facts, OutputModel)` → result either returned to
the UI or turned into exactly one proposal.

**U9 Procurement** (background + staff), four flows on top of the PQ-01 groundwork:
1. *Request quotes:* reorder suggestion (or a staff click) → agent reads the suggestion and the item's suppliers →
   `propose_quote_request` (which suppliers, quantity, needed-by) → after approval the existing executor sends the
   requests through the outbox with signed reply links.
2. *Read a reply:* a supplier reply arrives as structured form input (no AI needed) **or** as pasted free text → extraction
   (`fast_extract`) into a `SupplierQuoteExtract`; **every number must appear verbatim in the source text**, otherwise the
   field is left empty and flagged → `propose_supplier_quote` for a person to confirm.
3. *Compare and propose:* when all invited suppliers answered or the deadline passed → `compare_quotes` (deterministic
   score from PQ-01) → the agent explains the ranking and trade-offs in plain words → `propose_po_from_quote` for the
   recommended quote, with the comparison as evidence.
4. *Chase:* daily scan of late purchase orders and unanswered requests → `propose_supplier_followup` with a drafted
   message per case.

## 6. What code decides vs what the model decides

| Decision | Owner |
|---|---|
| Free slots, stock availability, reorder quantities, prices, totals, tax, currency conversion | code (existing functions) |
| Quote ranking score and its components | code (PQ-01 `compare_quotes`) |
| Which customer a message is from when ambiguous | code rule (thresholds) → otherwise `needs_human` |
| Intent, which service the customer means, which slot of the offered ones fits the request | model |
| Wording of replies, explanations, follow-ups, summaries, campaign copy | model, always reviewed by a person |
| Values extracted from supplier text | model, accepted only if each number appears in the text; person confirms |
| Whether anything happens | a person (approval) |

## 7. Failure handling

| Failure | Behaviour |
|---|---|
| Provider timeout / 429 / 5xx | router tries the next allowed route; all failed → run `failed`, `ai.provider_unavailable` |
| Invalid JSON or schema mismatch | one repair message; still invalid → `failed`, `ai.invalid_model_output` |
| Unknown or disallowed tool | observation `ai.tool_not_allowed`; counts as a step |
| Tool permission denied / validation error / domain conflict | observation with the error code; the model may adapt |
| Step limit reached | `step_limit`; intake posts a note "needs a person" |
| Budget exhausted | `budget_exceeded`; AI features show a banner until the next UTC day |
| Policy excludes every route (e.g. `regulated` without a BAA provider) | `policy_blocked`; features disabled with an explanation |
| Job crashes mid-run | job retries with the same dedupe key; the run row of the crashed attempt is closed as `failed` by the next attempt; tools are idempotent where they create holds (hold per proposal) |

## 8. Privacy and policy

- The context builder includes only what the use case needs; contact details only when a reply needs them.
- Routing honours the business policy: `hosted_vultr` (default), `hosted_any`, `strict_private`, and `regulated`
  ([ADR-018](../03-decisions.md#adr-018-regulated-data-profile-health-clinics-later-by-configuration)), which allows only
  providers marked `baa: true` and local models and is forced when `DATA_PROFILE=regulated`. Nothing falls back to an
  excluded provider.
- Outbound HTTP uses `app.platform.http.client()` and its egress allowlist; provider hosts must be listed in
  `EGRESS_ALLOWLIST`.
