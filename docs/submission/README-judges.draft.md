# SMB Operations Suite

SMB Operations Suite is an AI-assisted ERP for small businesses. It brings customers, appointments, stock, messages, sales and purchasing into one app. A business can install it on its own Vultr account. The demo uses synthetic data for Example Cycle Works, Example Garden Store and Example Dental Clinic.

**Demo:** https://45-63-85-83.sslip.io · Version 0.1.10, according to the deployment ledger. A demo video is not yet available.

## What it does

| Area | What staff or customers can do |
|---|---|
| Customers | Keep profiles, contact preferences, consent and a timeline. |
| Schedule | Find available times, hold and book appointments, and reserve parts. The database prevents overlapping bookings. |
| Catalog and stock | Manage services and parts, stock movements, reservations and reorder suggestions. |
| Inbox | Read public chat messages, add notes and hand conversations to a person. |
| Approvals | Review, edit, approve or reject proposed changes. Decisions and execution are audited. |
| Sales | Create quotes, orders, invoices and credit notes; export accounting data and propose payment reminders. |
| Purchasing and procurement | Manage suppliers, quote requests, comparisons, purchase orders and receipts. The assistant can read pasted supplier replies and explain a comparison; code calculates the scores. |
| Marketing | Prepare campaigns using consent and suppression checks. |
| Knowledge base | Publish articles for cited answers, search by text and embeddings, and import PDFs as drafts for review. |
| AI Team | View agents, their switches, budgets, work and permitted hand-offs. Ask an agent for help in the assistant. |
| Customer Care | Propose check-ins and recall reminders, and hand complaints or disputes to staff. |
| Voice dictation | Dictate into supported staff text fields and review the text before sending. |
| Website widget | Embed chat on website addresses the business allows. |
| My bookings | Eligible customers use an emailed link to view, cancel or reschedule upcoming bookings without an account. |
| Example Dental Clinic | Synthetic clinic data demonstrates emergency hand-over and appointments that require both a provider and a chair. |
| Today and System | See work needing attention, delivery problems and backup status. Owners can export data. |

## Architecture

The React frontend talks to a FastAPI modular monolith. PostgreSQL stores business data and uses row-level security for tenant isolation. A worker handles background jobs. Keycloak handles sign-in and Caddy provides TLS. Modules own their data and communicate through defined interfaces. See the [architecture diagram and details](docs/roadmap/02-architecture.md).

## How Vultr is used

| Service | Use in the demo |
|---|---|
| Compute | One `vhp-2c-4gb-amd` VM runs the application stack with Docker Compose. |
| Serverless Inference | The [route file](deploy/config/ai-routes.yaml) selects hosted models through a gateway. Agent: `mimo-v2.6-flash`, fallback `deepseek-v4.1-flash-1017`; extraction: `mimo-v2.6-flash`, fallback `qwen3.8-flash-next`; writer: `deepseek-v4.1-flash-1017`, fallback `mimo-v2.6-flash`; grounding: `deepseek-v4.1-flash-1017`, fallback `qwen3.8-flash-next`; safety: `mimo-v2.6-flash`; embeddings: `qwen3-embedding-4b`; speech-to-text: `whisper-large-v3-turbo`. |
| Object Storage | Encrypted backups; a restore drill recovered the demo databases. |
| Container Registry | Stores versioned application images. |
| Terraform | The `vultr/vultr` provider provisions the demo infrastructure; cloud-init and install scripts configure it. |

The [spend ledger](docs/vultr-spend.md) records $7.52 in pending charges against $200 of credit as of 7 Nov 2026 03:15 UTC. This is a snapshot, not a current bill.

## AI design and safety

The assistant uses permission-checked tools to read information and create proposals. Staff review proposals before execution. A temporary booking hold is an exception to proposal-only writes. The system can also act under explicit autonomy rules: rules have limits and can be paused, and demo rules start in shadow mode. Shadow mode records what a rule would do without performing it. Some enabled rules can act and notify; AI-sent messages have a delay and a Stop control. Staff can take over a conversation.

The AI Team includes Front Desk, Inventory, Purchasing, Finance, Marketing, Operations Manager and Customer Care. Each agent has controls and a daily budget. Hand-offs follow a fixed allowlist, with a chain budget of 6 steps, 30 model calls, 10 actions and $0.50. Customer Care checks consent, contact preference, quiet hours, duplicates and open disputes before sending.

Front Desk answers from published business articles with citations. A separate grounding check tests claims against the cited material; unsupported answers go to a person. Customer and supplier text is treated as untrusted data, not instructions. Prices, availability and quote rankings come from code. Supplier numbers extracted by AI must appear in the source text. These safeguards reduce risk; evaluations cannot prove immunity to every injection.

A `regulated` installation allows only models on the operator's private network. It does not fall back to hosted APIs. In the [local benchmark](backend/evals/reports/2026-11-06-local-models.md), grounding and extraction passed; the tested model did not pass the agent and writer criteria. Those agent features and summaries remain off in that profile until a local model passes. The dental demo is synthetic; its banner says real patient data needs models under the clinic's control. This is not a compliance certification.

## Evaluations and tests

These are measured synthetic-case results, not guarantees. Reports are in [backend/evals/reports](backend/evals/reports/).

| Suite | Result |
|---|---:|
| [Intake](backend/evals/reports/20261106T233716655394Z-intake-vultr-mimo-v2.6-flash.md) | 75/76 |
| [Injection](backend/evals/reports/20261106T232900803303Z-injection-vultr-mimo-v2.6-flash.md) | 15/15 |
| [Grounding](backend/evals/reports/20261105T222935011343Z-grounding-vultr-deepseek-v4.1-flash-1017.md) | 10/10 |
| [Customer Care](backend/evals/reports/20261106T205753238190Z-customer_care-vultr-deepseek-v4.1-flash-1017.md) | 19/19 |
| [Retrieval](backend/evals/reports/20261107T042934404525Z-retrieval-vultr-qwen3-embedding-4b.md) | 8/8 (paraphrased questions; the right article is cited) |
| [Dictation](backend/evals/reports/20261106T155040898824Z-dictation-vultr-deepseek-v4.1-flash-1017.md) | 87.5% (scores the clean-up step, which uses the writer model after `whisper-large-v3-turbo` transcribes) |

The [0.1.10 release notes](CHANGELOG.md) record 2,398 backend tests at 96% coverage, 554 frontend tests and 35 end-to-end tests. A [demo VM performance run](docs/performance-local.md) with 20 staff over 5 minutes recorded 204 ms p95 and 0 errors in 5,251 requests. The [security review](docs/security/2026-11-event-review.md) recorded no high findings, one accepted medium CSP finding and an open egress hardening item.

## What was built before and during the event

The non-AI ERP, including sales, purchasing and procurement, was built before kickoff. No AI code, prompts, inference calls, agent loops or evals were built before kickoff. The AI design was documented in advance. All AI functionality was built after kickoff at **3 Nov 2026 15:00 UTC**, including the gateway, agents, prompts, tools, evaluations and voice dictation. The dated records are [PRE_EVENT_WORK.md](PRE_EVENT_WORK.md) and [EVENT_WORK.md](EVENT_WORK.md). Later non-AI features also appear in event work.

## Setup

For local development, follow the repository prerequisites and run `make dev`. To install on a Vultr account, follow the [install runbook](docs/runbooks/install.md), including `deploy/scripts/install.sh` and its dry run. These operations require the operator's own configuration and credentials.

## Limitations and next steps

- The demo has no SMTP configured. Email is simulated, including supplier messages and self-service links. There are no real customers or patient records.
- From 0.1.11 the route file carries Vultr's own catalog prices (`GET /v1/models`, read 7 November 2026); costs recorded by earlier versions are overstated (input was charged at the output price, audio at a $0.006/min placeholder). The catalog is not an invoice: compare with the Vultr bill. Spending and latency vary with use.
- Application-level outbound destinations are restricted, but raw container egress is open. An egress proxy is tracked as P8-09. The security review also accepts an inline-style CSP finding.
- There is no payment processing or general ledger. Direct accounting integration and inbound email are not built. Backups retain erased data until expiry; six-hourly backups can lose changes since the last backup.
- The local benchmark is on an M1 Pro workstation, not the demo VM or clinic hardware. Regulated use needs suitable locally controlled models, infrastructure, agreements and independent review.
- Next: complete the egress proxy, compare recorded costs with the Vultr bill, rehearse and record the demo, and test a stronger local model against the same suites.

## License

MIT
