# SMB AI operations suite product scope and workflows

Design draft for founder and implementation team | 24 October 2026 UTC | Pre-event planning only

## Recommendation

Build a general small-business operations suite around one shared customer record and one staff approval queue. CRM, conversations, scheduling, inventory and marketing should feel like connected parts of the same business, rather than separate chatbots. The AI proposes useful next steps; deterministic services validate and execute them after the required staff approval.

The first working slice should demonstrate an enquiry becoming a customer record, a proposed appointment and an inventory reservation, followed by staff approval and an auditable outcome. A campaign draft can show how the same customer context supports marketing. Browser voice is an input channel, not a separate product or a promise of telephone integration.

Keep the broad suite vision. Do not try to make every module production-ready during the hackathon. A convincing vertical slice with realistic failure handling is stronger than six disconnected, superficially complete screens.

## Intended customers and boundaries

Initial fit: service businesses and small retailers that combine appointments, customer enquiries and simple stock tracking. Examples include repair shops, salons, tutoring businesses and specialist stores. A dental practice is an optional later example, not the core market, data model or demo. Do not use patient records or imply healthcare compliance.

The long-term deployment preference is one customer-owned installation in the customer's Vultr account, with self-hosted data and models where privacy requires it. This is not a shared SaaS tenant pool by default. Customer ownership does not remove operational responsibilities or cloud-provider access risks.

Non-goals for the first release: payroll, accounting ledger, payments, autonomous purchasing, employee evaluation, clinical decisions, omnichannel telephony, enterprise ERP replacement or unrestricted autonomous agents. A draft purchase request is not an order. A campaign draft is not a sent campaign.

## Product structure

- Today: work awaiting attention, conversations needing staff, upcoming appointments, low-stock warnings and pending approvals.
- Customers: contacts, organisations where needed, consent preferences, interaction timeline, notes and linked work.
- Conversations: browser text and optional browser voice, source-linked summaries, handoff to staff and extracted requests.
- Schedule: services, resources, working hours, appointments and short-lived holds.
- Inventory: products, stock locations, stock movements, available quantity, reservations and reorder suggestions.
- Marketing: consent-aware audience preview, draft content and approval history; external sending is a later gated integration.
- Approvals and audit: exact proposed changes, evidence, approver, expiration, execution status and receipts.
- Settings: staff roles, business timezone, retention, deployment mode, connectors and inference policy.

## Roles and authority

Owner/admin configures the business, grants staff access and approves sensitive configuration changes. Operator handles assigned customers and appointments. Inventory manager approves stock adjustments. Marketing manager reviews audience and content. Read-only auditor reviews evidence without changing operations.

Permissions are enforced on the server, per organisation and operation. An AI response, browser transcript or imported document cannot grant permission. Public visitors may submit enquiries and view their own validated booking flow; they cannot query staff CRM or stock records. Never use possession of a conversation identifier as authentication.

## Core workflows

### Enquiry to approved appointment

1. Visitor or staff enters a request in browser text or explicitly starts microphone capture.
2. System stores the conversation under the correct business and produces a proposed intent, customer match and relevant facts with source references. Uncertain matches require staff selection; do not merge contacts automatically.
3. Scheduling service reads current availability, business timezone, service duration and resource constraints. Inventory service checks any required product or part. The model does not invent availability.
4. Assistant creates an action proposal showing the customer, exact slot and timezone, affected stock, intended message, recipients and any price information sourced from the catalogue.
5. Staff reviews, edits or rejects it. Editing consequential fields produces a new proposal revision and invalidates any earlier approval.
6. On approval, backend rechecks permissions, record versions, slot availability and stock. It atomically commits local appointment and reservation changes, then records an outbox item for any authorised message.
7. UI shows committed state and a separate delivery state. If an external calendar or message provider times out, show an uncertain result and reconcile before retrying. Never claim a customer has been notified just because a local record exists.

First demo uses the suite's own calendar and a clearly labelled simulated delivery adapter. External calendars come later. This avoids presenting a mock integration as a real booking or message.

### Conversation to customer service follow-up

Summarise the thread, list unresolved questions, attach factual evidence and propose a reply. Staff can correct the customer match, summary and response. Missing facts become a question or escalation, not an invented policy. A source containing “ignore your rules” remains data. The model has no direct connector credentials or write access.

Browser voice uses visible start/stop controls, recording indication, permission handling and a text alternative. Confirm names, dates, quantities and consequential requests in text before approval. Handle interruptions, disconnections and speech errors. Raw audio retention is off by default. Text remains the dependable fallback if speech latency or privacy requirements cannot be met.

### Inventory exception to reorder suggestion

A stock movement or reservation takes available quantity below a configured threshold. Show on-hand, reserved and available amounts separately, and explain the calculation. Assistant proposes a reorder quantity using explicitly configured lead-time and demand assumptions. Staff reviews the suggestion; no supplier order or payment is placed in the hackathon slice.

Stock is a movement ledger with reservations, not a number the model overwrites. Prevent negative availability unless the business explicitly enables backorders. Cancelling an appointment records a reservation-release event and restores available quantity without changing physical on-hand stock. A compensating stock movement is appropriate only if a physical stock movement actually occurred. Use integer quantities for discrete units and explicit units/decimal precision where relevant.

### Marketing draft from eligible customers

Staff chooses a campaign purpose and an eligible audience. The backend filters consent, suppression, channel availability and business scope. AI writes a draft grounded in approved offers and stock/service facts. Preview exact audience count, sample recipients, channel and content; let staff inspect the full recipient set. Approval binds the audience snapshot and content revision. Recheck suppressions immediately before any future send.

The demo stops at approved draft and simulated delivery. Real sending requires a verified connector, appropriate consent, unsubscribe handling, rate limits and explicit authorisation. Do not infer marketing consent from a booking or support conversation.

## Shared records and consistency

Proposed entities: Business, StaffUser, RoleGrant, Customer, ConsentPreference, Conversation, Message, EvidenceReference, Service, Resource, Appointment, InventoryItem, StockMovement, Reservation, CampaignDraft, AudienceSnapshot, ActionProposal, Approval, ExecutionAttempt, OutboxItem and AuditEvent.

Every business record carries a business identifier, stable ID and version. Store event timestamps in UTC and preserve the business/customer timezone needed to explain appointments. Imported messages retain source IDs for deduplication. Avoid unnecessary personal attributes. Embeddings, if introduced later, are sensitive derivatives with the same scope and deletion obligations as their source.

Proposal states: draft, pending review, approved, rejected, expired, executing, succeeded, failed or needs reconciliation. Approval is not execution success. Bind approval to the exact action type, targets, payload hash, record versions, policy version, approver and expiry. A changed recipient, slot, amount or audience requires renewed review.

Use database transactions and uniqueness constraints for local conflicts. Use durable outbox processing and idempotency keys for side effects. There is no promise of exactly-once delivery across external providers: retain provider operation IDs, reconcile unknown outcomes and avoid blind retries. Partial workflows expose each completed and pending step rather than a misleading all-or-nothing success banner.

## AI responsibilities and limits

AI can extract, classify, summarise, retrieve relevant context and produce schema-constrained proposals. Business services own permission checks, availability, arithmetic, consent, policy enforcement and mutations. No arbitrary shell, SQL or unrestricted URL tools. Limit context to the selected customer's authorised records; do not let a model-chosen business ID select a different tenant.

Model quality must be measured on representative tasks: customer ambiguity, noisy speech, wrong dates, prompt injection, contradictory stock information, unsupported claims and missing context. Model choice and hardware sizing remain provisional. AI-generated implementation can accelerate work, but schema drift, API mismatches, migrations, concurrency bugs, security mistakes and untested assumptions remain real integration risks.

## Hackathon delivery plan

The proposed fit is Challenge 2, Future of Work. The event requires a Vultr VM-backed web deployment; Serverless Inference is optional and event GPUs are unavailable. See the [event requirements](https://lablab.ai/ai-hackathons/vultr-hackathon). These constraints make a CPU-first or explicitly permitted hosted-inference demo appropriate; neither proves a future private GPU installation.

Stage 0, now: review these designs, preserve dated provenance, identify uncertainties and prepare a test specification. This stage adds no application code, infrastructure or account changes. Earlier local SQL/fixture preparation is separately disclosed in the parent PRE_EVENT_WORK.md; it is not erased by this design-only stage.

Stage 1, during the event after rules are reconfirmed: implement authentication, business scoping, core records, seeded fictional data, proposal state machine and audit trail. Acceptance: operator cannot read another business's data; unauthorised mutations fail server-side; proposal edits invalidate approval.

Stage 2: implement the enquiry-to-appointment vertical slice with internal calendar and stock reservation. Acceptance: two concurrent bookings for the same resource cannot both succeed; duplicate approval clicks do not duplicate mutations; cancellation releases stock; stale proposals return a clear conflict.

Stage 3: connect the selected inference runtime and add source-linked summaries and structured proposals. Acceptance: invalid model output cannot execute; a malicious instruction in a message cannot gain tools or access; timeouts leave recoverable state. Use a declared test set and record actual results, not claims of “safe AI.”

Stage 4: add campaign drafting and optional browser voice if the core slice is stable. Acceptance: suppressed contacts are excluded; voice denial and interruption fall back to text; raw audio is not retained in private mode without explicit configuration.

Stage 5: harden the demo, test a clean install, exercise backup restoration, record a walkthrough and document known gaps. Publication, deployment, spend and GitHub changes require later authorisation. Do not label unfinished modules as working functionality.

Post-event pilot: real integrations, operational monitoring, restore drills, privacy review, support ownership and measured load tests. Multi-site stock, advanced campaigns and additional verticals follow evidence from pilot users rather than expanding the first demo.

## Demonstration and evidence

Use fictional repair-shop data: a customer requests service, the assistant finds a suitable slot and part, staff approves, and the timeline shows the appointment plus stock reservation. Show a second request losing a slot conflict, a rejected proposal leaving records unchanged and an injected instruction being ignored. Finish with a marketing draft and the same audit history.

Capture actual completion rate, unsupported-fact rate, invalid-action rejection, approval correctness, duplicate prevention, time-to-first-response and end-to-end latency. Report sample size, hardware, inference mode and failed cases. Do not promise a throughput, latency or cost target until measured.

## Decisions still needed before implementation

Confirm the first fictional business example, demo inference mode, which module is the stretch goal, and the approved build/start boundary under the event's rules. Decide later, not now, on cloud region, spend cap, domain, credentials, live integrations and any customer data. No production readiness, regulatory compliance or organiser exception is established by this document.

## Review clarification: authority and durable outcomes

Before execution, revalidate the approver's current role and business scope as well as proposal expiry and versions. Revoked approval or role removal prevents a queued action from starting. Use an atomic execution claim and stable idempotency key; concurrent workers cannot independently execute the same proposal. For effects already in flight, report uncertainty and reconcile; revocation cannot guarantee reversal of an external effect.

Commit local mutations, execution outcome, audit event and outbox record together. Audit persistence failure must roll back the local mutation. External receipts are recorded separately after delivery; crash recovery reconciles an ambiguous provider result. Approval records are immutable decisions, distinct from the proposal's mutable lifecycle. Owners must decide which sensitive operations require a second reviewer; do not silently promise separation of duties.

Inventory is a stretch goal if it threatens delivery of the appointment/CRM slice. Voice and marketing remain optional. These are requirements for future implementation, not working controls.
