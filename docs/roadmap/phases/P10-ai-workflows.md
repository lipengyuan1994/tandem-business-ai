# P10 · AI workflows: from drafts to an AI team that does the work

**Status: APPROVED (owner, 2026-11-05, in chat: "Approve as proposed").** D1–D4, D6, D8–D10 as proposed in §11;
D5 and D7 decided 2026-11-06 by the tech lead under the owner's standing approval ("use your best judgment"):
**D5 = (a)**, plus the local benchmark of E0 run on a developer Mac (no GPU VM unless those results need one);
**D7 = 120 s / 4 MB**, confirm before send kept. Each step becomes cards under `docs/roadmap/ai/cards/` (contracts first) and rows in `STATUS.md`.
Reviewed by a second model (GPT-6.1 Sol, read-only, 2026-11-05); its corrections are folded in below.

**Goal:** the AI does routine work end to end, inside rules the business sets. People approve what is risky and can
see, stop and correct what the AI did. Customers get a front desk that answers from the business's own knowledge,
books, and hands over to a person when needed.

## 1. Where we are (checked in the code)

| Capability | Today | Gap |
|---|---|---|
| Staff assistant (copilot) | Chat with up to 21 of the 26 tools, filtered by the user's permissions (`ai/usecases/copilot.py`). Tools read, hold a slot (written immediately) or **propose**; proposals wait on the Approvals page; after approval an executor runs | Nothing that changes data or contacts anyone runs without a separate approval, even when the person asking may approve it. The copilot has no purchasing or payment-reminder proposal tools. "Order this from the supplier" cannot finish |
| Executors | Book, reschedule, cancel; send reply; create/update customer; stock adjustment; reorder draft; PO from draft; approve PO; quote requests, supplier quote, PO from quote, supplier follow-up; create quote; confirm order; payment reminder; approve campaign | **No delivery of a PO to the supplier**: the manual "send" transition only marks a PO sent (`purchasing/service.py`). No undo or compensation for any action |
| Customer front desk | Visitor chat at `/p/<slug>/chat`: intake answers stock/service/price/hours, asks clarifying questions, proposes bookings | Every reply waits for staff. No knowledge base, no embed snippet, no customer self-service. Hand-over exists in the backend (request recorded, thread status, event, staff notified) but **the AI does not stop**: intake does not check it |
| Coordination | Events and deduplicated jobs already chain work: low stock → quote requests → supplier replies → comparison (`ai/subscribers.py`) | Not presented as a team; no view of "what the AI did and is waiting on" |
| Background vs on request | Scheduled: procurement and payment scans. On request: briefing (cached per user and hour), campaign draft (writes a campaign revision directly) | — |
| Voice | Spec only (M14: push-to-talk, ≤ 60 s / 2 MB, confirm before sending) | The gateway only supports chat; STT, embeddings and re-ranking need gateway adapters, cost accounting and tests. Vultr lists `whisper-large-v3-turbo`, `qwen3-embedding-4b`, `bge-reranker-v2-m3`, `chatterbox`, `qwen3-tts` |
| Regulated data | ADR-018/ADR-020 (`DATA_PROFILE=regulated`); per-business `ai_policy`; P10-E0 routing requires hosted:false + vendor:local + private resolved addresses, with policy rechecked per attempt; BAA-hosted AI excluded | Routing evidence in [E0 card](../ai/cards/P10-E0-routing.md). Routing task does not deploy or benchmark local models (P5-02); clinic network verification remains operator-owned |
| Scheduling for clinics | One resource per appointment | A dental visit needs provider **and** chair at once |

## 2. Product principles

1. **The AI does the work; the business sets the rules**, per action, not per product.
2. **One path for every action:** proposal → decision → executor, whether a person or a rule decides. One audit trail.
3. **Earn autonomy:** every rule runs in **shadow mode** first ("would have acted"), with a preview against past
   cases; it is switched on only when its measured error rate is acceptable.
4. **Risk sets the default.** Start conservative; customer-facing silent action is never a default.
5. **Executed is not delivered:** the UI distinguishes queued, delivered, failed, unknown, stopped and corrected.
6. **Customers know it is AI** and can always reach a person.
7. **Fail closed** for regulated data.
8. **Measure outcomes:** bookings completed, cash recovered, stock-outs avoided, staff time saved, wrong actions.

## 3. Workstream A — Autonomy engine (Tier A)

**Levels** per action type and business: `off` (AI may not propose it) · `draft` (AI writes, a person sends) · `ask`
(today) · `act_notify` (runs; shown in Activity; stoppable while queued) · `act` (runs; logged). A failed condition
lowers `act`/`act_notify` to `ask`; it never raises `off` or `draft`. Rules govern **AI proposals only**, never a
person's manual proposals.

**Decisions by a rule are delegated, not invented.** `approval_decisions.decided_by` must be a user today, and
execution re-checks that user's permissions. Change: a decision is either *human* (user, as now) or *policy* (rule id,
rule version, the owner/admin who granted the rule, and the permissions the rule delegates). Execution checks the
grantor still holds those permissions and the rule is still active. Revision and payload-hash binding stay.

**Checks at execution, not only at creation:** kill switch, rule still active, customer not restricted, business
preconditions (slot free, stock, invoice still unpaid), quiet hours. **Caps** (messages per day, money per day) are
reserved atomically, so concurrent proposals cannot exceed them.

**Repeated intent:** effect keys stop retries, not the same intent from a new message. Add domain keys (e.g. "reminder
stage 1 for invoice X", "open replenishment for item Y").

**Stop before sending:** outbox gets `not_before` (checked in the claim transaction) and a `cancelled` status that
competes atomically with the claim; once `sending`, Stop is no longer possible and `unknown` stays as today. **Web chat
replies** become visible when their row commits, so they need a publication state (`withheld` → `published`).

**Races with staff and customers:** reply payloads carry the thread generation and the last inbound message they answer;
a staff takeover or a newer customer message cancels queued automatic replies.

**Undo = compensation.** No generic undo flag. Each executor declares which results can be compensated and how (cancel
a booking that has not started, reverse a stock adjustment), as a linked, audited compensation proposal with version
checks. A booked slot freed later is not "undone" for the customer, so they are told.

**Confirm in chat:** the assistant shows the action card; **Confirm** calls the normal decision endpoint with the exact
revision and hash (a typed "yes" never approves). Allowed when the requester holds the approve permission and the
action is not marked second-person. This replaces the business-wide `allow_self_approval` switch for
assistant-requested actions only (D2).

**Proposed starting defaults** (D1) — all new rules start in shadow mode:

| Action | Start | Possible later (owner opt-in, after shadow results) |
|---|---|---|
| Reorder draft from low stock (internal, deterministic quantity) | `act_notify` | `act` |
| Front-desk answer that is a DB fact (stock, hours, price, service duration) or a cited knowledge-base answer | `ask` (shadow) | `act_notify` when unsupported-answer rate is low |
| Clarifying question to a customer | `ask` (shadow) | `act_notify` |
| Create customer from chat (deduplicated, no merging) | `act_notify` | — |
| Book a free slot the customer explicitly confirmed, listed service, known price | `ask` | `act_notify` |
| Customer reschedules/cancels own booking in self-service (authenticated link, fee acknowledged) | `act_notify` (not an AI action: a customer action) | — |
| Supplier quote request; late-PO follow-up | `ask` | `act_notify` with approved templates and suppliers |
| Send PO to supplier | `ask`, requester may confirm in chat up to a limit (total incl. tax/shipping, per day, anti-splitting) | — |
| PO above the limit or to a new supplier | `ask`, second person | — |
| First payment reminder (template only, payment re-checked before delivery) | `ask` | `act_notify` |
| Stock adjustment | `ask` | — |
| Marketing campaign send | `ask`, second person, always | — |
| Refunds, discounts, price changes | `off` in P10 | — |
| Regulated business: anything patient-specific | `ask`; administrative public answers kept separate | — |

**UI:** **AI → Autonomy** (levels, limits, kill switch, shadow results and preview), **Activity** (what ran, delivery
state, Stop, Correct), and a **Work queue** (what is waiting, on whom, deadline, blocker) plus an **Exceptions** list
(failed deliveries, model or e-mail outages, stopped workflows).

## 4. Workstream B — AI team

**Agents are a product layer over the existing events and jobs**, not a second task system: each agent is a
configuration (name, job, tools, triggers, autonomy rules, model class, budget) and a page. A workflow-instance record
is added only where work must wait durably (e.g. "waiting for supplier quotes until Friday").

| Agent | Job | Built from |
|---|---|---|
| **Front Desk** | Answers customers, books, collects details, hands over | intake |
| **Customer Care** (post-sales) | "Ready for pickup", after-service check-in, review request, complaint/warranty triage, recall reminders (dental 6-month) | new |
| **Marketing** | Segment ideas, campaign drafts, calendar, results | campaign draft |
| **Inventory** | Low-stock watch, reorder drafts, parts for tomorrow's bookings, count anomalies | reorder note |
| **Purchasing** | Quotes, comparison, PO, delivery to supplier, chasing late orders, goods received | procurement |
| **Finance** | Overdue invoices, reminders, payment-plan proposals | payment follow-up |
| **Operations Manager** | Morning briefing; routes staff requests to the right agent; watches stuck work and budgets | briefing + copilot |

**Safe chaining:** every chain carries a root correlation id and causation id; allowed transitions are a fixed graph
(e.g. Inventory → Purchasing, never Purchasing → Marketing); one active workflow per subject; workflow-wide budgets on
model calls, actions and cost; deadlines and terminal states. Customers are told delivery dates only from confirmed
evidence (a supplier's confirmed date), never from an agent's guess.

**UI:** **AI Team** page (card per agent: on/off, autonomy, today's work, cost, waiting items); chat with the Manager or
"@Inventory …"; one activity timeline with completion evidence (PO delivered, supplier acknowledged, goods received,
invoice paid).

## 5. Workstream C — AI front desk for customers

Build in this order:

1. **Hand-over enforced:** "Talk to a person" button; the AI stops on takeover or request (subscribe to the existing
   event; intake checks thread state); after-hours message.
2. **Knowledge base (new module M19), first version:** owner-written text articles, published explicitly, public only,
   versioned; Postgres full-text search; answers cite article id and version. Prices, hours, stock and durations always
   come from the database, never from articles.
3. **Grounding check:** structured claim-to-source checks (each claim → supporting article span; outcomes supported /
   unsupported / contradicted). Unsupported → staff; the question becomes a suggested article. The same model checking
   itself shares its blind spots, so the gate is measured in shadow mode (unsupported-answer rate, latency, cost) and a
   different model class is evaluated for the check.
4. **Embed snippet** for the shop's website and **customer self-service** ("My bookings" by magic link).
5. **Later:** embeddings and re-ranking (gateway work), PDF upload (publication approval, parser limits, tenant and
   visibility filtering before retrieval, deletion propagation, malicious-document tests). Retrieved text never grants
   tools, changes rules or exposes internal content; `<untrusted>` fencing is guidance, not a boundary.

Disclosure: "You're chatting with <Business>'s AI assistant", plus the existing privacy notice per `ai_policy`.

## 6. Workstream D — Voice dictation (like Typeless)

Speak, get clean text: fillers removed, spoken corrections applied ("Tuesday, no, Wednesday" → "Wednesday"),
punctuation and lists, the business's names and SKUs spelled right, nothing added.

- Mic in the assistant box and long text fields; hold-to-talk or toggle; shortcut; recording indicator; text fallback.
- Pipeline: `MediaRecorder` → `POST /api/v1/voice/transcriptions` → STT adapter (new gateway capability; Vultr
  `whisper-large-v3-turbo`, local Whisper under `strict_private`) → polish pass with a **scoped** dictionary (catalog
  items and the current screen's names, not every customer) → editable text; raw transcript kept beside it.
- Keep M14's rule: the person confirms before anything is sent (immediate send is a later option). Length limit:
  D7 (owner, 2026-11-06) amends M14 to 120 s / 4 MiB; backend accepts provider measurement up to 125 s.
- Tests and evals: negation, quantities, currencies, dates, corrections, names; codecs, byte limits, interrupted
  uploads, temp-file cleanup, concurrency; audio never stored.

## 7. Workstream E — Dental clinic example and local models for PHI

- **Example Dental Clinic** (synthetic): dentist and hygienist, chairs, services (exam and cleaning, emergency visit,
  whitening consultation, new-patient visit), 6-month recall, supplies, knowledge base (insurance accepted, new-patient
  forms, emergency policy, cancellation fee), terminology (Patient, Provider, Treatment), `avoid_medical_details`.
  No charts, clinical notes, imaging or insurance claims (ADR-018).
- **Needed first:** appointments that reserve **several resources** (provider + chair); explicit emergency escalation
  (always to a person, with the clinic's emergency instructions); recall eligibility rules.
- **Required statement** (dental demo data, demo script, README, banner on that business's AI screens): *"Synthetic
  demo data. In production, patients' protected health information (PHI) and other personal data must be processed
  only by AI models hosted locally under the clinic's control — no external AI API. Compliance also needs the clinic's
  HIPAA policies, agreements and a legal review."*
- **Enforcement:** ADR-018 change (D6): `regulated` = local routes only, for every model class (agent, writer, safety,
  extraction, STT, polish, embeddings, indexing) and every fallback; policy re-checked when it changes mid-run. A route
  marked `hosted: false` is configuration, not proof: the install checks document how to verify the model server is
  inside the clinic's network.
- **P5-02 benchmark early** (parallel to A): the smallest local models (vLLM / llama.cpp / Ollama, OpenAI-compatible)
  that pass the existing eval suites, and the hardware they need. A rented GPU test proves compatibility, not
  clinic-local operation.

## 8. Order of work (each step ships and is demoable)

| Step | Content | Size |
|---|---|---|
| **A0** | Smallest autonomy slice: low-stock event → existing quantity calculation → rule-approved reorder draft → Activity entry with link. Policy decisions (delegated), execution-time checks, caps, kill switch, shadow mode, domain keys. Tests: duplicate trigger, concurrent cap, kill switch, stale stock, executor crash | M, Tier A |
| **A1** | Confirm in chat; copilot purchasing tools; **PO delivered to the supplier** (`purchasing.send_po`); outbox `not_before`/cancel; executed vs delivered states; Work queue and Exceptions | L, Tier A |
| **C1** | Hand-over enforced; knowledge base v1 (text, full-text, citations); grounding check; front-desk answers in shadow → `act_notify` when the numbers allow; web-chat publication state; reply race preconditions | L |
| **E0** | P5-02 local-model benchmark; ADR-018 local-only change (parallel, no UI dependency) | M |
| **D** | Voice dictation (parallel; needs the STT gateway adapter) | M |
| **B** | AI Team page, agent configs over existing workflows, correlation ids and transition graph, Customer Care agent | L |
| **E1** | Multi-resource booking, emergency rules, recall, Example Dental Clinic data and banner | M |
| **C2** | Embed snippet, customer self-service, embeddings and PDF upload | M |

Every step: contracts in `docs/roadmap/ai/contracts.md` first; Codex cards (Sol; Tier A for approvals, outbox,
routing); new eval cases (should-ask vs may-act, grounded answers, handoff chains, dictation); `make check`; live evals;
release to the demo VM.

## 9. What an owner will expect (kept in scope)

Work queue with owner, deadline and blocker; completion evidence; quiet hours and contact preferences; business rules
for cancellation fees and deposits; disputes stop reminders; preview of a rule against history before switching it on;
exceptions and clear degraded behaviour when a model, e-mail or integration fails; outcome metrics.

## 10. Risks

Wrong action without a person (shadow mode, conservative defaults, execution-time checks, caps, Stop, kill switch);
hallucinated answers (DB facts, citations, grounding gate measured before use); loops and cost (transition graph,
workflow budgets, installation USD ceilings); injection through customers or documents (untrusted input never grants
tools; documents published by the owner); regulated data (fail closed, local-only routes, verified hosting).

## 11. Owner decisions

- **D1** Starting defaults in §3 (conservative; everything customer-facing starts as `ask` in shadow mode).
- **D2** Confirm in chat counts as the requester's approval when they hold the approve permission, except
  second-person actions (yes/no). PO confirm-in-chat limit (default proposal: $500 per order, $1,000 per day).
- **D3** Agent roster in §4 (add Reviews/Reputation? Staff rostering?).
- **D4** Knowledge base v1 with full-text search only; embeddings and PDFs in C2 (proposed).
- **D5** (decided: a) Dental demo AI on the public demo: (a) Vultr models on synthetic data with the banner and a "demo only"
  marker that a regulated install refuses; (b) AI off for that business with the explanation; (c) also a short
  local-model test on a rented GPU VM (≈ $1–2 per hour, within the credit).
- **D6** ADR-018: `regulated` = local models only (proposed, per the owner), or BAA-covered APIs as an explicit opt-in.
- **D7** (decided: 120 s / 4 MB) Voice: Vultr Whisper for standard businesses; keep confirm-before-send; 60 s or 120 s limit.
- **D8** Order of work in §8.
- **D9** New libraries when their step starts (PDF text extraction in C2; none needed for A0–C1).
- **D10** Shadow-mode thresholds for switching a rule on (proposal: at least 50 shadow cases and ≤ 2 % wrong
  "would act" decisions, reviewed by the owner).
