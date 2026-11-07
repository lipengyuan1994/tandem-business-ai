# Changelog

Release notes for operators. Each release lists database migrations and any manual step (with "before" or "after" the upgrade). The upgrade runbook is `docs/runbooks/upgrade.md`.

## 0.1.15 (2026-11-07)

- **Assistant knows today's date:** asked "which mornings next week are free?", the staff assistant searched August
  instead of November because, unlike the customer intake, it was not told the business-local date. It now is. New
  eval case `copilot-next-week-slots`; live assistant eval 23/25 (earlier runs 78–83 %; the two failures reached the
  right reorder proposal without the expected lookup step).
- **Approvals layout:** a long reply no longer stretches the Approve button and the page sideways; long evidence
  values wrap.
- Both found while recording the demo video.

- **Migrations, deployment files, route file:** none.

## 0.1.14 (2026-11-07)

- **Answers from the knowledge base:** asked what a service includes ("what does a tune-up include?") or about
  holidays, the assistant now searches the business's published articles and cites them, instead of describing the
  service from its name (intake prompt v8). Prices and durations still come from the catalogue.
- **Guardrail without false alarms on catalogue prices:** the guardrail (prompt v2) treats prices, durations, stock and
  opening hours as catalogue values, because the separate fact check verifies them; it still holds discounts, deals,
  free items and invented prices for a person. Replies checked by the earlier guardrail version fail closed.
- Live evals: intake 76/78 on two runs (new cases for service contents and holidays), reply guardrail 16/16 on two
  runs. Found while recording the demo: two live answers were held by the fact check because the assistant described a
  tune-up from memory; nothing incorrect was sent.

- **Migrations, deployment files, route file:** none.

## 0.1.13 (2026-11-07)

- **Automatic answers for routine questions:** with a live reply rule, the public chat and website widget answer
  ordinary questions by themselves (after the send delay, with a Stop control for staff). Complaints, warranty claims,
  disputes, safety concerns and emergencies always wait for staff.
- **The business's own guardrail:** Settings → AI has a field for plain-language rules ("a person approves any discount
  or completion date"). A model checks every drafted reply against them before it may be sent automatically; if a rule
  might be broken, or the check fails or times out, a person approves the reply first. Changing the rules cancels
  automatic sending of replies checked under the old rules. Approvals shows why a reply waits.
- **Honest privacy note:** the chat page says the assistant answers routine questions by itself only when the business
  actually has a live reply rule; otherwise it keeps "staff review every reply".
- **Demo data (on reset):** Cycle Works answers automatically with a guardrail and a 15-second delay; Garden Store stays
  in shadow mode; Dental Clinic approves every reply. Existing installations keep their settings.
- Checks: 2,443 backend tests (96 % coverage), 576 frontend tests. Live evals: reply guardrail 14/14 (about 0.7 s and
  $0.00007 per check), intake 74/76, Customer Care 18/19. Browser end-to-end tests were not re-run for this release.

- **Migration (applied by `upgrade.sh`):** `20261107_1600_reply_guardrail` (records the new AI use case). No
  dependency, service, container or model-route change.

## 0.1.12 (2026-11-07)

- **New name:** the product is now **Tandem Business AI** (by OpenDL): browser tab, sidebar, sign-in page and API
  title. Internal names (`smb-*` images, realm `smb`, Terraform) are unchanged.
- **Demo address:** the demo moved to `https://tandemai.open-dl.com` (done on the VM before this release; see the
  install runbook section "Move a running installation to your own domain").

- **Migrations, deployment files, route file:** none. **Sign-in display name (existing installs, optional):** the
  realm file is imported only once, so set the realm display name in Keycloak (done on the demo).

## 0.1.11 (2026-11-07)

- **Real AI prices:** the route file now uses Vultr's own catalog prices (`GET /v1/models`, read 2026-11-07): input and
  output tokens priced separately, Whisper at $0.000667 per audio minute. Earlier versions charged input tokens at the
  output price and audio at a $0.006/min placeholder, so costs recorded before this release are overstated. Daily
  limits now go further for the same budget.
- **Public chat:** when a visitor hits the hourly limit for new conversations, the message no longer promises "a minute"
  ("Too many messages right now. Please try again later.").
- The website widget is verified in Safari's engine (WebKit) as well as Chromium.

- **Migrations, deployment files:** none. **Route file (after):** upload the new `deploy/config/ai-routes.yaml` after
  `upgrade.sh 0.1.11` (keep `config/ai-routes.yaml.previous`), then `docker compose restart api worker`.

## 0.1.10 (2026-11-07)

Finishing touches to the AI workflows:

- **Work queue in priority order:** emergency hand-overs first, then complaint/warranty/dispute cases (urgent, high,
  normal), then everything else newest first, across all pages. Complaint rows show the category and the customer.
- **AI Team:** each agent shows what starts its work (for example "Appointment completed", "Daily recall check"); the
  chain view shows how deep a chain of work went ("Depth 2 of 6").
- **Assistant:** people who may use the assistant but not see AI approvals can now address agents with `@` and see
  when the Operations Manager is off.
- **Website chat widget:** shows nothing at all on a website the business has not allowed; the open panel no longer
  covers the top of the chat.

- **Migrations, deployment files, route file:** none.
- Checks: 2,398 backend tests (96 % coverage), 554 frontend tests, 35 end-to-end tests including new ones for
  "My bookings", the widget across sites and a dental emergency hand-over.

## 0.1.9 (2026-11-07)

**Example Dental Clinic** (P10-E1): a third synthetic demo business showing the same product configured for a clinic.
Treatments can require a provider **and** a chair; the database makes it impossible to double-book either, including
holds. Emergency handling (off by default, on for the clinic): messages about pain, swelling, bleeding, trauma or
anything urgent go straight to a person with the clinic's own instructions and "call your local emergency number"; the
AI never answers or books them. Six-month recall reminders through Customer Care, without treatment names. The clinic's
screens and public chat show the required statement that real patient data needs AI models hosted under the clinic's
control; a `regulated` installation refuses such synthetic demo businesses.

**Customer Care screens** (P10-B2): settings (check-in delay, quiet hours, tone), review and booking links in the
public profile, complaint cases in the Work queue with Resolve, each customer's preferred contact channel.

**Smarter knowledge search and PDF import** (P10-C2a): the front desk finds the right article when visitors word things
differently (Vultr `qwen3-embedding-4b` embeddings combined with full-text search; full-text only when embeddings are
unavailable or not allowed). Owners can turn a PDF into draft articles to review and publish; the PDF itself is not
kept.

**Website chat widget and "My bookings"** (P10-C2b): paste one line into your website to show the chat there (only
on the website addresses you list in Settings). Customers can see, cancel or reschedule their bookings from an e-mail
link, without an account (Settings → self-service; the cancellation notice and fee text are yours to set).

- **Migrations:** `20261106_1300_knowledge_embeddings`, `20261106_1400_dental`, `20261106_1500_self_service`
  (applied by the upgrade; the `vector` extension is already installed by the database init script).
- **Route file (after):** new `embed` route in `deploy/config/ai-routes.yaml`. Upload it **after** `upgrade.sh 0.1.9`
  (keep `config/ai-routes.yaml.previous`), then `docker compose restart api worker`. The embedding price is a
  placeholder ($0.05 per million input tokens): confirm Vultr's billing.
- **Web image:** Caddy serves `/embed.js` and `/embed/<business>/chat` with a per-business frame policy.
- Existing businesses keep self-service off until enabled in Settings; articles are indexed for search by the hourly
  job (or Knowledge → reindex).
- Checks: 2,385 backend and 537 frontend tests, 30 end-to-end tests; live evals intake 75/76, injection 15/15,
  grounding 10/10, customer care 19/19, retrieval 8/8.

## 0.1.8 (2026-11-06)

**Customer Care** (P10-B2, backend): a new agent that keeps in touch after the work is done — a "How did it go?"
check-in a few days after a completed appointment (with an optional review link), recall reminders when a service is
due again ("time for your 6-month check-up"), and complaint/warranty triage that puts the concern in front of a person
with the related appointment and invoice and a suggested reply. Every message is a proposal first; before it is sent
the system rechecks consent, the customer's contact preference, open complaints or disputes, quiet hours (default
20:00–08:00) and duplicates. Clinics and businesses that avoid medical details get minimal messages without service
names, written without AI. **Customer Care is off until you switch it on in AI → Team**; its screens (settings for the
check-in delay, review link and recall interval) come in a later release.

**Front desk:** messages that complain about earlier work, ask for a refund or claim warranty now go straight to a
person instead of an AI reply. Ordinary repair requests ("my brake squeaks, please book a brake service") are not
affected. Customers' preferred contact channel (e-mail, web chat, none) is stored; a consent ticked in the chat widget
covers that request only, never later reminders.

- **Migration:** `20261106_1200_customer_care` (applied by the upgrade).
- **Deployment files and route file:** unchanged; nothing to copy.
- Live evals (Vultr): customer_care 19/19, intake 69/70, injection 15/15.

## 0.1.7 (2026-11-06)

**Voice dictation** (P10-D): a microphone button in the assistant, the Inbox reply box, the knowledge editor and other
long text fields. Speak, and clean text is inserted for you to check: fillers removed, spoken corrections applied
("twelve, no, ten" → 10), your catalogue and supplier names spelled right. Nothing is sent until you send it; audio
and transcripts are never stored. Up to 2 minutes per recording. Speech-to-text: Vultr `whisper-large-v3-turbo`.

**AI Team** (P10-B1, B3): AI → Team shows the Front Desk, Inventory, Purchasing, Finance, Marketing, Operations
Manager and (coming) Customer Care agents, each with an on/off switch, a daily cost limit, today's work, waiting items
and its timeline; a chain view shows how one piece of work passed between agents. Address an agent in the assistant
with `@Inventory …`. Work that one agent hands to another follows a fixed set of allowed hand-offs and a budget per
chain (6 steps, 30 model calls, 10 actions, $0.50).

**Clinics and private data** (P10-E0): the `regulated` profile now uses only AI models on the clinic's own private
network (no hosted API, also not BAA-covered ones); `/api/v1/ai/status` reports per model class whether it is
available and why not (AI settings show this for voice dictation).
A benchmark of a local 20B model on a workstation is in `backend/evals/reports/2026-11-06-local-models.md`.

- **Migrations:** `20261106_0800_voice_dictation`, `20261106_1000_ai_team` (apply with the upgrade). Existing agents
  start **on**, so nothing changes until someone switches one off.
- **Route file (after):** new `stt` route with `price_per_audio_minute` in `deploy/config/ai-routes.yaml`. Upload it
  **after** `upgrade.sh 0.1.7` succeeds (keep the old file as `config/ai-routes.yaml.previous`), then
  `docker compose restart api worker`; 0.1.6 rejects the new field, so a rollback with it in place would run without
  AI. The audio price is a placeholder ($0.006/min): confirm Vultr's audio billing.
- **Microphone:** the web image's Caddy now sends `Permissions-Policy: microphone=(self)` (built into the image).
- **Deployment files:** `compose.yml` changed in comments only; copy it to keep files in step, no other step.

## 0.1.6 (2026-11-06)

AI workflows, first steps (ADR-020): autonomy rules (Off · Draft · Ask · Act and notify · Act, shadow mode, daily limit,
pause switch) with rule-approved reorder drafts, AI → Autonomy, Activity, Work queue and Exceptions pages; action cards
with **Confirm** in the assistant chat (own requests only; $500 per order, $1,000 per day; larger or new-supplier orders
need a second person); purchase orders e-mailed to the supplier; AI-sent e-mails wait 2 minutes and can be stopped.

Front desk (P10-C1): **Knowledge** pages (articles, versions, publish, suggestions from unanswered questions); the
assistant answers customers from published public articles with citations, checked claim by claim by a second model;
**Talk to a person** in the chat widget stops the AI (after-hours message; unassigned hand-overs now notify staff);
staff **Take over** / **Hand back to AI** in the Inbox; AI web-chat replies wait for the send delay and can be stopped;
front-desk replies can be decided by an autonomy rule (starts in shadow mode).

- **Migrations:** `20261105_0800_autonomy`, `20261105_0900_chat_delivery`, `20261105_1000_knowledge_handoff`,
  `20261105_1100_grounded_publication` (apply with the upgrade).
- **Route file (after):** new model class `grounding` in `deploy/config/ai-routes.yaml`. Upload it **after**
  `upgrade.sh 0.1.6` succeeds (keep the old file as `config/ai-routes.yaml.previous`), then
  `docker compose restart api worker`. Not before: 0.1.5 rejects the unknown class, so after an automatic rollback the old
  version would run with AI turned off. Until the file is replaced, front-desk replies fail the check closed and go to a person.
- **Deployment files unchanged** (`compose.yml`, scripts): `upgrade.sh 0.1.6`.
- **New business setting:** `outbound_send_delay_seconds` (default 120). Demo seed adds a reorder-draft rule in shadow mode.

## 0.1.5 (2026-11-04)

Marketing: a **Draft with AI** button on the campaign page writes a new revision through the campaign-draft AI use case
(people who may edit campaigns and use the assistant; disabled while there are unsaved edits; a campaign in review asks
for confirmation first, as manual edits do). Restore drill: the details file `/state/restore-last.json` now lists every
database of the last run and is also written when row counts differ (it was missing in that case).

- **No migration.** Backup image rebuilt (restore helper). **Scripts changed:** `restore.sh` — copy it from the staged
  release to `/opt/smb/restore.sh` (mode 755) before or after `upgrade.sh 0.1.5`; `compose.yml` unchanged.

## 0.1.4 (2026-11-04)

Intake customer matching: an e-mail or phone that matches no customer now outweighs a name-only overlap, a shared surname alone is no longer a possible identity (an initial plus surname still is), and an ambiguous match no longer stops intake: the agent still answers information questions and the staff note lists the candidates. Found live on the demo deployment (a new visitor sharing a surname got no reply to a stock question).

- **No migration, no deployment-file change:** `upgrade.sh 0.1.4`.

## 0.1.3 (2026-11-04)

AI quality from live evaluations (`backend/evals/reports/2026-11-03-selection.md`): extraction uses `mimo-v2.6-flash` first; supplier values with units or currency words ("USD 12.00 each", "10 pairs") are parsed while verification against the supplier text stays strict (extraction prompt v2); intake prompt v3 answers stock questions from tool results, asks instead of choosing for an undecided customer, and acknowledges and hands off cancel/refund/unknown requests. All six live eval suites meet their thresholds.

- **No migration.** Upload the updated route file (`deploy/config/ai-routes.yaml` → `/opt/smb/config/ai-routes.yaml`) and run `docker compose up -d api worker` after `upgrade.sh`; deployment scripts unchanged since 0.1.2.

## 0.1.2 (2026-11-04)

Production-readiness release after an operations review (`docs/security/2026-11-03-ops-readiness-review.md`): host watchdog with webhook alerts, stricter backup health, worker capacity and drain, database pool sizes and timeouts, daily retention cleanup, streamed exports, hard AI spending ceilings and a per-business limit on public-chat AI runs. Upgrade in a maintenance window; existing VMs also need the one-off root step `install-host-ops.sh` (see `docs/runbooks/upgrade.md`).

- **Host monitoring (batch F; no database migration):** five-minute watchdog checks HTTPS readiness, service health/restarts/OOM, disk/inodes, backup and maintenance age, stuck/dead jobs, certificate expiry and time sync. **Existing VM manual step:** stage `watchdog.sh`, `watchdog.py`, `install-host-ops.sh`, updated `smoke.sh` and `compose.yml`; run `/opt/smb/install-host-ops.sh` once as root through the Vultr console. `upgrade.sh` alone does not install the timer. Add `ALERT_WEBHOOK_URL=` to both protected environment copies, configure/test receipt with `watchdog.sh --test`, assign primary/backup operators, and configure an independent external uptime check. See `docs/runbooks/monitoring.md`.
- **Backup health:** a failed attempt or last success older than **7 hours** is unhealthy; `backup.sh --status` exposes independent prune/check results and success timestamps. **After upgrade:** run `docker compose exec backup backup.sh --maintenance` once in a quiet window to initialize maintenance status; failures/missing status or success older than eight days alert. Backup image rebuild required; six-hour schedule and retention are unchanged.
- **Maintenance window required:** updated `compose.yml` gives API/worker 120-second shutdown grace and PostgreSQL a 60-second idle-in-transaction timeout (applies on PostgreSQL recreation). Drain writers before migrations; follow `docs/runbooks/upgrade.md`. Journal limits are now 256 MiB persistent / 64 MiB runtime, 1 GiB reserve, 14 days. Follow the project-only image/restore-database cleanup procedure in `monitoring.md`, preserving the previous release and the pinned migration image.
- **New-VM host baseline:** cloud-init requires key-only SSH (root network login `prohibit-password`), limits UFW SSH sources to the same IPv4/IPv6 admin CIDRs as Vultr, enables/verifies NTP, and installs the watchdog timer. **Existing VMs:** review/apply SSH and UFW through the console per `docs/runbooks/access.md`; changing Terraform inputs does not rerun cloud-init. Keep host SSH sources aligned when opening/closing provider rules, including after installer `--close-ssh`.

- **Platform/worker (batch E):** apply migration `20261104_0700_retention` (after `20261104_0600_ai_budgets`) before starting the new API/worker, in the maintenance window. Daily indexed cleanup removes completed/dead/failed jobs and fully delivered events after 30 days, expired/revoked/idle sessions and OIDC states, rate windows after 48 hours, and cached idempotency responses after their existing 24-hour TTL. Full 500-row batches continue a minute later. Audit stays; compact job dedupe receipts stay permanently. **Rollback limitation:** older application code that ignores receipts can replay old actions after cleanup; schema downgrade refuses to drop nonempty receipts. See `backend/app/platform/OPERATIONS.md`.
- **Worker settings:** four total threads now reserve two for operational jobs; events get a turn even with a job backlog. `WORKER_AI_CONCURRENCY` defaults to 2 and must be less than `WORKER_CONCURRENCY` (now minimum 2; a two-thread installation must explicitly set AI capacity to 1). `WORKER_SHUTDOWN_GRACE_SECONDS=100` waits for running AI/mail/jobs on SIGTERM; keep Compose stop grace at least 120 seconds, and increase it if increasing application grace. Heartbeat now reflects executor progress/idle polling; continue monitoring queue age and dead work.
- **Application database settings:** API pools default to 4 + 2 overflow connections; worker pools to 6 + 0 (12 total for one process each), controlled by `API_DB_POOL_SIZE`, `API_DB_MAX_OVERFLOW`, `WORKER_DB_POOL_SIZE`, `WORKER_DB_MAX_OVERFLOW`; `DB_POOL_TIMEOUT_SECONDS=10`. Each application connection defaults to `DB_STATEMENT_TIMEOUT_MS=30000` and `DB_IDLE_TRANSACTION_TIMEOUT_MS=60000`; migration/backup clients are exempt from these application settings. Defaults need no configuration; forward custom values into the corresponding container and restart it. Multiply budgets for replicas and cap Keycloak separately; see the platform operations notes.

- **AI cost/abuse controls (batch G):** migration `20261104_0600_ai_budgets` adds installation cost counters seeded from existing usage, public allowances and notice deduplication. Apply the migration in the maintenance window before restarting the API/worker. No data reset is needed; old pending intake jobs are retained.
- **AI operator settings:** installation ceilings default to `AI_DAILY_COST_LIMIT_USD=5.00` / `AI_MONTHLY_COST_LIMIT_USD=50.00`; each process allows `AI_MAX_CONCURRENT_CALLS=2` active attempts. Every provider attempt, including fallbacks/repairs, reserves conservative input plus maximum output; failures without usage and crashes retain the reservation. Usage/80%/100% notices include in-flight reservations. Accurate route prices and separate provider-side billing/restrictions remain operator responsibilities.
- **Public intake:** `AI_PUBLIC_INTAKE_PER_HOUR=30` per business per UTC hour and `AI_PUBLIC_INTAKE_MAX_PENDING=30` queued/running jobs. Over-limit messages remain stored with a staff handoff and outcome note, with no safety/agent call. Intake and safety share `AI_PUBLIC_DAILY_TOKEN_BUDGET_PER_BUSINESS=200000`, capped at 25% of the business daily tokens to preserve staff capacity; staff copilot/summary do not spend the public hourly quota. Zero public quotas disable public AI while retaining messages for staff.
- **AI independent budget notices:** set `AI_BUDGET_NOTIFICATION_EMAIL` to an operator address in both protected environment copies, configure SMTP, then recreate API and worker; the updated production Compose file forwards all seven new settings to both services. The notification outbox queues 80%/100% notices as real mail only with SMTP; otherwise delivery is labeled simulated. Verify actual receipt independently. Without an address, notices remain in-app: the identity public API does not expose staff email addresses. Stage the updated production Compose file using the existing deployment-file upgrade procedure; merely editing the host environment does not forward new settings into containers.

- **Streaming owner exports (batch E):** rebuild/redeploy the API image; no additional migration or manual configuration. ZIP downloads now stream database batches and compression output directly, including module redactions, without staging the dataset/ZIP in memory or tmpfs. JSON members, manifest/counts, filename, owner-only access, rate limit and the completed-export audit event remain compatible; the streaming ZIP container uses ZIP64/data descriptors. The final ZIP directory is sent only after the audit transaction commits. Failed/disconnected transfers release their transaction and must be retried; a stalled client can hit normal database timeouts. See `backend/app/platform/OPERATIONS.md`.

- **Intake context stability:** contact redaction preserves typed service/customer/part UUIDs and numeric facts. UUIDs with long numeric suffixes no longer fail intake as apparent phone numbers; free-text phone numbers and email addresses remain masked. No additional migration or manual step.

## 0.1.1 (2026-11-03)

AI hardening after the first live tests and a second-model review: live routes use non-reasoning Vultr models in JSON-object mode; intake needs about 5 steps instead of 9; procurement prices respect each currency's decimals and a supplier currency that cannot be verified blocks the proposal; one AI outcome per inbound message; read permissions enforced for every AI tool; deadlines and input-size limits on every model call; copilot allows one answer at a time per thread and accepts an `Idempotency-Key`; briefing cache tied to the user's permissions; per-target jobs for procurement and payment scans. New error codes shown in plain words in the app.

- **Migrations:** `20261104_0400_ai_operations`, `20261104_0500_ai_operation_details` (no manual step).
- **Deployment files changed** (`install.sh`, `upgrade.sh`, `restore.sh`, new `install-ai.sh` and `wait-backup-healthy.sh`): follow the "deployment files change" procedure in `docs/runbooks/upgrade.md`. The installer now waits for the backup service to report healthy before the smoke test.
- **To enable AI** on an install: upload a route file as `config/ai-routes.yaml` (start from `deploy/config/ai-routes.yaml`), set `VULTR_INFERENCE_API_KEY` and add `api.vultrinference.com` to `EGRESS_ALLOWLIST` in `.env` (both copies), then `docker compose up -d api worker` (see `docs/runbooks/install.md`).

## 0.1.0 (not yet released)

First version: platform, core services, business modules (CRM, catalog, inventory, scheduling, conversations, dashboard) without AI, packaging and deployment files. No upgrade path from earlier versions (there are none).

Also in 0.1.0: marketing campaigns, System page, owner data export and customer erasure (migrations `20261027_0900_admin_ops_core` and `20261027_1500_erasure_hardening`; no manual step).

Also in 0.1.0: sales (quotes, orders, invoices, credit notes, accounting CSV; migrations `20261028_0900_sales_core`, `20261028_0910_sales_tax_rate`; set the default sales tax rate under Settings → Policies after upgrading).

Also in 0.1.0: purchasing (suppliers, purchase orders, goods receipts; migration `20261028_1000_purchasing_core`; no manual step).

Also in 0.1.0: e-mail over SMTP (set `SMTP_URL` to send real e-mail; without it messages stay simulated; configure Keycloak mail by hand, see `docs/runbooks/email.md`).

Also in 0.1.0: regulated data profile (`DATA_PROFILE`, default `standard`; migrations `20261028_1100_regulated_profile`, `20261028_1200_regulated_hardening`; see `docs/runbooks/regulated-profile.md`) and per-business terminology under Settings.

Also in 0.1.0: payment reminders for overdue invoices; procurement quote requests with a public supplier quote page, explained comparison, purchase orders from quotes and late-order follow-ups (migrations `20261031_1000_platform_prerequisites`, `20261031_1100_invoice_reminders`, `20261031_1200_purchasing_quotes`; route `/q/*` served by the public bundle — check custom proxies).

Also in 0.1.0: supplier quote e-mails and payment reminders stay simulated without `SMTP_URL`, like other notifications (they failed before); approval previews show item names and amounts in normal units; staff can copy a pending supplier's quote link. No migration, no manual step. Seeding demo data never sends real e-mail, even with `SMTP_URL` set.

Also in 0.1.0 (security hardening, 2026-11-03): the web (Caddy), PostgreSQL and Keycloak containers run without root and with all capabilities dropped (Caddy keeps only the right to bind ports 80/443); Caddy sends no `Server` header and masks capability tokens and login codes in its error log; Keycloak's master realm is no longer reachable through Caddy; the API description (`/api/v1/openapi.json`, `/api/v1/docs`) is off in production; 429 answers carry `Retry-After`; new command `demo-logins` creates logins for the synthetic demo staff only (see `docs/runbooks/install.md`). **Deployment files changed** (`compose.yml`, `upgrade.sh`, `smoke.sh`): follow the "deployment files change" procedure in `docs/runbooks/upgrade.md`; `upgrade.sh` hands the Caddy volumes to the new non-root user before starting it (a manual upgrade must run the chown step in that runbook first, or certificates cannot be read).

Also in 0.1.0 (AI layer, built after the event kickoff on 2026-11-03): module `ai` (migration `20261103_1600_ai_core`): model gateway (OpenAI-compatible incl. Vultr Serverless Inference, Anthropic), router with per-business policies and a daily token budget, agent runner with run/usage recording, 26 tools (read, slot hold, propose-for-approval), and use cases: inbound-message intake, staff copilot chat (server-sent events), conversation summary with a suggested reply, reorder notes, daily briefing, procurement agent (quote requests, supplier reply extraction, comparison, PO proposal, follow-ups). Every change the AI suggests is a proposal a person approves. **Manual steps:** create `config/ai-routes.yaml` from `deploy/config/ai-routes.example.yaml` (install and upgrade never overwrite it) and set `VULTR_INFERENCE_API_KEY` in `.env`; without both, AI stays off ("not configured"). Deployment files changed (`compose.yml`, `Caddyfile`, `install.sh`, `upgrade.sh`, `smoke.sh`, `env.prod.template`): follow the "deployment files change" procedure in `docs/runbooks/upgrade.md`.
