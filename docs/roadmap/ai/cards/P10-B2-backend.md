# P10-B2 backend — Customer Care

Tier A / EVENT, 2026-11-06. **DONE — backend and review fixes verified; release separate.**
Contract: [P10-B2: Customer Care](../contracts.md#p10-b2-customer-care).
Builds on B1/review migration `20261106_1000_ai_team`; migration head is
`20261106_1200_customer_care`. Only the isolated `smb_team` database is used.

## Behavior and decisions

- Customer Care remains **disabled by default**. Its check-in, recall and complaint jobs use B1 admission,
  agent/day and chain budgets, correlation/causation metadata and existing durable jobs. No new service,
  dependency or task engine. Demo rules are `ask`, shadow true. Complaint replies are excluded from rule
  execution by the executor's eligibility callback, rechecked by approvals at policy execution.
- One `customer_care.send_message` proposal addresses one customer. Approval permission is
  `conversations.proposal.approve`. Payload contains kind, bounded subject/body, customer, appointment/service/date,
  optional invoice/source-message/recall month and aware `not_before`. Appointment and source-message facts are verified against the tenant.
  Unique subject receipts and serialized customer locks prevent two approved proposals sending the same subject;
  stopping a queued delivery does not release its subject receipt for another send.
- CRM contact preference is `email|web_chat|none`, default email. Proactive email requires explicit
  `service_updates`; a review request/link additionally requires marketing consent. Source web chat stays in its
  authenticated existing thread; public thread contact evidence never authorizes an unrelated proactive email.
  Inactive, restricted, erased, opted-out or disputed customers cannot receive proactive reminders.
- Check-ins default to two days; zero disables them. Completion events include the completion timestamp, so a
  delayed subscriber does not restart the whole delay. Review links come from the existing public-profile settings.
  The optional `review_url` and `booking_url` accept bounded absolute HTTP(S) URLs.
- Recall intervals are nullable service-only values, 1–120 calendar months. Month arithmetic clamps month ends.
  The bounded daily scan uses each customer's latest completed service, starts 14 days before its original due
  date, excludes future confirmed/unexpired held appointments and requires consent. Domain identity is
  customer + service + original due month, not scan month. Each subject has a stable independent chain root;
  per-day discovery jobs allow later reconsideration without duplicate proposals.
- Business-local quiet hours default 20:00–08:00. Integer start/end hours are configurable; equal hours disable
  the window. Scheduling accounts for `not_before` and A1's AI delay together, and checks the window again at
  actual delivery. Consent, current contact preference/address, erasure/restriction and complaints are also
  rechecked there. Outbox/Stop and C1 publication/Stop retain their existing locking and states.
- Intake v6 and safety v2 classify complaints, warranty claims and disputes independently of safety flags.
  Classification immediately creates a human case/hold; the care job attaches the latest completed appointment,
  relevant issued invoice and separately reviewed reply. Unknown customers remain visible Work items requiring
  identification. Cases have normal/high/urgent priority and an explicit versioned human resolution endpoint.
  Human-approved complaint web replies may publish within the captured human ownership generation; later input
  or takeover still invalidates publication. Classification never decides coverage, refunds or medical advice.
- `customer_care_message` v1 uses the writer class, a strict JSON schema and only first name, service/date,
  business, allowed links, language and tone. Notes never enter the writer. Invalid/unavailable/policy-blocked/
  global-budget-blocked generation falls back to deterministic templates. Templates cover English, Spanish,
  French and Chinese; unavailable fallback languages are skipped with `customer_care.language_unavailable` for human writing. Regulated or `avoid_medical_details` bypasses
  AI and uses minimal copy without service names or notes, including after a profile change before delivery.
  B1 agent/chain stops and disabled admission cannot be bypassed by a template.
- Activity includes queued/delivered/stopped/skipped evidence and fixed reason codes. The team card counts care
  runs, proposals, executions, handoffs and open human cases. Cases and receipts export; their contents are IDs
  and fixed classifications only. Existing proposal/run/outbox/conversation erasure owns message-text redaction.
- TypeSafe was considered and its skill read. Existing gateway classification fits the authorized stack;
  no provider, dependency, hosted judgment or live documentation/model request was added.

## Files

- `ai/customer_care/{schemas,models,policy,writer,executor,delivery,workflows,triage,api}.py`: payloads, cases,
  receipts, eligibility, generation, execution, guarded publication, triggers, Work projection and resolution.
- `ai/{agents,schemas,models,registration,lifecycle,team_service,team_activity,work_queue_api,api}.py`:
  B1 ownership, storage/export and owner-facing projections. `agent/{protocol,runner}.py`, `tools/intake.py`,
  `usecases/intake.py`, prompts `customer_care_message.v1`, `intake.v6`, `safety.v2`: classification and writing.
  Existing prompt versions are preserved.
- CRM model/schemas/service/public API: preference, omission-preserving proposal serialization, internal locked contact DTO and consent/contact serialization.
  Catalog model/schemas/service/public API: recall configuration and service discovery.
  Scheduling `care.py`, public API and completion event: bounded latest-history/future-booking reads and timestamp.
  Sales public API: related invoice reference. Org schemas/public API: care settings and profile links.
- Approvals autonomy allowlist and execution callback; platform registry delivery callback; notifications
  service/templates and conversations public/replies/publication: existing delayed delivery and Stop integration.
- Migration `20261106_1200_customer_care.py`; `backend/openapi.json`; two synthetic ask/shadow seed rules.
- `tests/integration/{customer_care_helpers,test_customer_care,test_customer_care_triggers,test_customer_care_writer}.py`;
  AI export/structural table expectations and admin export table expectations. Existing intake, runner, permission-sweep and registry tests are updated for the extended interface. `evals/{runner,runtime}.py`,
  `evals/suites/customer_care.yaml`, 19 cases in `evals/cases/customer_care/`.
- Module README cards, this card, contracts, STATUS, HANDOFF and EVENT_WORK. No frontend files.

## Requirement evidence

| Requirement | Evidence |
|---|---|
| Proposal/decision/executor; preferred channel and payload facts | `test_checkin_pending_human_with_facts_and_dedupe`, email/web execution cases |
| Consent revoked, erased, preference, open complaint/dispute | `test_execution_rechecks_live_customer_state` and `test_delivery_rechecks_after_executor` |
| Delay, Stop, duplicate domain key including concurrent approvals | `test_delay_stop_and_duplicate_domain_key`, `test_simultaneous_approved_proposals_queue_one_domain_message` |
| Quiet hours include send delay and reschedule at delivery | `test_quiet_hours_next_allowed_time`, `test_quiet_hours_include_delay_and_reschedule` |
| Completed → delayed check-in; zero off; agent disabled | `test_completed_event_job_produces_checkin_only_after_delay`, `test_completed_event_delay_zero_and_disabled` |
| Recall eligibility, consent, future appointment, due-month dedupe | `test_recall_eligibility_and_due_month_dedupe`, `test_recall_exclusions`, calendar-month cases |
| Complaint classifications create immediate hold and human work | Both classifier branches in `test_intake_classification_creates_immediate_dispute_hold` |
| Priority, appointment/invoice, suggested reply, human resolution | `test_complaint_human_work_and_resolution`, `test_complaint_links_related_invoice_and_exports_case` |
| Complaint never rule-executes; unknown customer stays visible | `test_complaint_actual_nonshadow_rule_still_requires_human`, unknown-customer card/Work case |
| Web delay/Stop and explicit human approval while handed over | `test_complaint_web_reply_delay_and_stop`, `test_human_approved_care_web_reply_publishes_while_handed_over` |
| Regulated/minimal bypass, profile switch, no notes/injected offers | `test_regulated_bypasses_model`, minimal/profile-switch cases, writer rejection and notes isolation cases |
| Optional review link and marketing consent revocation | `test_review_link_requires_marketing_consent_and_rechecks_revocation` |
| Language/tone, deterministic fallback, team stops and off rules | Writer parametrizations, unavailable-generation cases, control and off-rule regressions |
| Maximum-length fallback preserves facts and whole URLs | 12 cases in `test_maximum_fallback_keeps_complete_facts_and_link` |
| Unrelated customer update cannot reset contact opt-out | `test_unrelated_crm_proposal_preserves_contact_opt_out` |
| Trigger/run/proposal/execution/outbox correlation and export | Completed-event integration, `test_correlation_run_proposal_execution_outbox_export`, case export |
| Shadow path | `test_autonomy_shadow_never_sends` |
| ≥15 eval cases; fallback cannot verify model quality | 19 cases through real offline harness; `test_eval_template_fallback_does_not_verify_model_quality` |
| Migration upgrade/downgrade; forced RLS and API permissions | Full migration round trip, architecture RLS/route sweeps and full backend suite |

## Verification

Native arm64 Python 3.13 and both psycopg compiled extensions verified. No installs, downloads, Docker,
external model/API calls, commits, pushes, releases or cloud actions. All data is synthetic.

- Initial business-rule tests failed before implementation (missing care module).
- Existing focused regression pass: **249 passed in 275.82 s**.
- Expanded care suite: **72 passed in 53.85 s**, including all 19 offline eval cases.
- Additional concurrent-send, human-only rule, invoice/export and migration checks passed. One new test had a
  missing test import, corrected and independently rerun with the off-rule and agent-ceiling cases: **3 passed**.
- Final completion-event regressions: **2 passed in 1.95 s**.
- The first full pass was intentionally interrupted after 306 passing tests when review found that an unrelated CRM proposal could reset an omitted contact preference. The omission is now preserved through immutable proposal serialization (following the existing sales serializer pattern); the actual proposal/executor regression passes. Unsupported template languages now skip instead of sending English; its regression also passes. Both final regressions: **2 passed in 2.00 s**.
- A later full pass was intentionally interrupted after 402 passing tests when a maximum-length fallback reproduced a truncated booking URL. The template now shortens fixed wording instead of cutting facts or links. All **57 writer tests passed in 43.51 s**, including 12 maximum-length cases; final classification/consent checks passed **3 tests in 2.58 s**.
- The first complete backend run finished **2,204 passed / 20 failed in 1,633.77 s**. All failures were old
  interface expectations: 17 intake-v5 assertions, one token-size fixture after optional protocol fields grew,
  the explicit AI endpoint permission list, and B1's reserved/no-trigger assertion. Expectations now require
  intake v6 while preserving versions 1–5, retain the original 80–99-token budget boundary, include both care
  endpoints in the permission sweep, and assert B2's exact triggers/default-off setting. No behavior assertion
  was removed. With a new cross-business/read-versus-resolve case permission regression, **22 passed in 37.90 s**.
- Final full scoped `make check`: **2,225 passed in 1,648.55 s (27:28)**, **96% coverage**; all 94 added
  Customer Care cases pass. Native Ruff/format, strict mypy, fixtures, backend licences and Gitleaks pass.
- Final eval-data tightening adds explicit language words and formal-tone exclusions. All **19 cases passed
  again in 37.01 s** through the real offline harness; product code is unchanged from the full pass.
- Independent database read confirms `current_database() = smb_team`, head `20261106_1200_customer_care`,
  and enabled/forced RLS on both care tables. Observed test configuration: SMTP unset, ordinary mail simulated.

Final scoped-check summary:

```text
All checks passed!
664 files already formatted
Success: no issues found in 352 source files
TOTAL  20270  804  96%
2225 passed in 1648.55s (0:27:28)
backend licences OK
no leaks found
make check: ALL PASSED
```

Scoped command selects existing `.venv` binaries using a temporary makefile, and passes all four application/
owner test/database URLs explicitly for `smb_team`:
`make -f Makefile -f /private/tmp/p10-b2-check.mk -o test-db -o frontend-test -o deploy-check check`.
The repository Makefile is unchanged. Frontend lint/typecheck/tests/licenses and Docker/deployment checks are
excluded. Backend fixtures, Ruff/format, strict mypy, full tests/coverage, licences and Gitleaks remain included.

**Not verified / not done:** live model quality, real external email delivery, frontend/TypeScript generation,
browser behavior and deployment. Offline FakeGateway cases validate wiring and deterministic scoring, not live
language quality or semantic injection resistance. Template fallback locales outside en/es/fr/zh require a person when model writing is unavailable or prohibited.
Unrestricted `make check` and an independent second-model review were not run in this backend-only task.

## Review fixes

Owner-requested independent review follow-up, 2026-11-06. All seven findings reproduced before product edits:
**21 failing cases in 10.65 s** (`/private/tmp/b2-review-before-valid.log`). The first reproduction attempt hit
the intended append-only consent-table protection in two fixture setups; those fixtures now append a withdrawal
and a later real public-chat grant. The valid rerun reproduces scheduled/delivered unsolicited mail, surviving
stale approved replies, missing cases, missing bound customer, unchanged recall delivery and unchecked text/invoice.
Delivery assertions were subsequently corrected to the established API contract (`skipped` return, cancelled
outbox row), and strengthened with fixed cancellation reasons and zero provider attempts.

| Finding | Fix and regression evidence (`tests/integration/test_customer_care_review.py`) |
|---|---|
| 1 — request consent authorizes proactive mail | `test_request_checkbox_never_authorizes_proactive_care` covers execution/delivery × check-in/recall/review. Current staff/import service-update evidence is required by shared policy; form grants are conservatively insufficient for proactive Care. `test_request_consent_preserves_chat_reply_but_blocks_proactive_web_care` preserves reply publication and blocks source-web proactive work. |
| 2 — stale approved complaint reply | `test_staff_intervention_invalidates_approved_care_reply` covers takeover and staff reply; Care participates in ownership invalidation. Payload and receipt snapshot `ai_generation`; all source-thread execution/delivery checks require that generation under the conversation lock. `test_care_executor_rejects_stale_generation_without_invalidation` simulates skip-locked invalidation, `test_staff_reply_after_care_execution_prevents_publication` checks delayed web publication, and `test_staff_intervention_also_blocks_email_care_reply` checks email at both boundaries. Human mode remains human. |
| 3 — triage skipped by reply admission | `test_complaint_triage_runs_independently_of_reply_admission` covers human ownership, open reply proposal and Front Desk disabled, semantic classification, durable hold, pending human reply and cancellation of previously queued mail. One classifier call, then replay with no additional call. `test_independent_classifier_obeys_enable_switch_and_public_cost_limit` checks Care disabled/hourly ceiling; `test_keyword_complaint_creates_hold_before_jobs_even_when_models_unavailable` checks immediate hold. |
| 4 — anonymous case lacks customer hold after linking | `test_linking_anonymous_complaint_binds_dispute_hold` checks same-transaction binding and queued-mail cancellation, including after Care is disabled. A second DB connection cannot acquire the customer advisory lock while the binding hook runs. |
| 5 — recall configuration not rechecked | `test_recall_rechecks_current_configuration` covers execution/delivery × inactive service, removed interval, changed interval and no-longer-due. Shared policy rechecks current interval/date, latest completion and future booking. Receipt stores the original due month. |
| 6 — Chinese review text bypass | `test_chinese_review_request_requires_link_and_consent` reproduces `请为我们留下评价。`; CJK and other non-space-separated review terms use substring matching, with Latin terms retaining word boundaries. |
| 7 — invoice evidence not scoped/matched | `test_care_invoice_must_match_scoped_customer_and_appointment` covers wrong customer, appointment, tenant and nonexistent invoice. `test_care_invoice_matching_customer_and_appointment_is_accepted` preserves valid evidence. Sales' public validator scopes the lookup; triage no longer falls back to an unrelated invoice when an appointment is known. |

Classification choice: a separate Care inbound subscriber and `ai.customer_care_classify` worker use the existing
safety v2 structured classifier. They bypass Front Desk ownership/open-proposal admission, never reply, and use
the shared public hourly model-run ceiling plus B1 agent, chain, policy and provider budgets. One durable job per
message, no job retries, normal bounded call/repair behavior; successful classifications/cases are reused.
Strong keyword signals can create an immediate conservative hold without a call. Other wording requires the
model; keywords are never a negative classifier. Ordinary unavailable classification asks a person to assess the
message and does not claim to have classified it; disabled/stopped/exhausted agent controls remain terminal.
No new dependency, provider, prompt or external request was introduced. FakeGateway tests verify classification
routing and effects; live semantic classification was not independently tested.

Preserved the owner's `evals/runner.py` `answer_any` and customer-care case edits. Added
`test_answer_any_requires_one_case_insensitive_term_from_every_group` in `ai/tests/test_eval_expectations.py`:
synonyms are alternatives inside each group, every group is required, and omitted groups remain optional.
The owner reports the hosted eval passing **19/19**; that live result was not independently rerun in this task.

Changed the existing uncommitted `20261106_1200_customer_care` migration (receipt `due_month` and `ai_generation`), then
downgraded/upgraded only `smb_team` from/to B1/B2. Additional changed surfaces: Care classification/policy/
payload/executor/delivery/workflows/triage/writer/models; CRM and Sales public helpers; conversation linking and
reply boundary; approvals invalidation; AI registry/public intake ceiling helper; platform hook; tests/docs/OpenAPI.
Backend-only restrictions remain: no installs, downloads, external calls, Docker, commits or deployment.

Review verification so far: first focused **124 passed in 66.71 s**, expanded edge cases **35 passed in 16.64 s**;
final focused Care/eval/team/conversation run **165 passed in 77.32 s**, including all **33 review regressions**.
The initial full run was intentionally interrupted after **592 passed in 497.89 s** when a review of the email
boundary found that queued complaint email also needed the ownership snapshot. Added its failing regression:
**2 failed in 2.39 s** (execution scheduled mail; delivery sent mail), then applied the shared generation check
and migrated the revised receipt. The final review suite now has **35 regressions**. Ruff, format and strict
mypy passed; OpenAPI regenerated. Final focused rerun: **167 passed in 78.64 s**.

Final full scoped `make check`: **2,261 passed in 1,667.80 s (27:47)**, **96% coverage**. All 35 review
regressions and the new `answer_any` unit test pass. Fixtures, Ruff/format (668 files), strict mypy (353 source
files), backend licences and Gitleaks pass. The full suite includes migration round trips, forced RLS,
permissions/export, OpenAPI freshness and existing chat-reply regressions. `git diff --check` passes.
Independent post-test readback confirms `current_database()=smb_team`, head `20261106_1200_customer_care`,
both receipt columns present, and enabled/forced RLS on both Care tables.

```text
All checks passed!
668 files already formatted
Success: no issues found in 353 source files
TOTAL  20412  810  96%
2261 passed in 1667.80s (0:27:47)
backend licences OK
no leaks found
make check: ALL PASSED
```

All seven findings: **reproduced and fixed**; none marked not reproduced. No live provider/semantic
classification or real mail delivery was exercised. The owner's hosted 19/19 report remains a user-reported
result, preserved with the eval changes. Frontend, unrestricted checks requiring Docker/frontend, deployment
and commits were outside this follow-up. The final scoped command and local native-runtime restrictions are
the same as the original verification above; final log: `/private/tmp/b2-review-full-final.log`.

## Reviewer verification (2026-11-06)
Live hosted evals (Vultr): `customer_care` first 14/19; the five failures were single required language words
(model used synonyms such as "turno"/"预约") and review-request cases demanding facts the prompt does not require.
Added an `answer_any` synonym-group expectation to the runner; rerun **19/19** (p95 5.1 s, $0.010). Intake with the
new safety v2 classifier dropped to 89.6 %: repair requests describing a symptom ("my brake squeaks, please book")
were classified as complaints and handed over. Tightened safety v2 with an explicit service-request vs complaint
boundary; refund case now accepts reply or direct hand-over (complaints go to a person per B2); added three
complaint/warranty cases (en, zh). Intake **69/70 (98.6 %)**, injection **15/15**. Also made
`preferred_contact_channel` optional on customer create/update (the generated TypeScript type made it mandatory,
so the edit dialog would have had to send "email" and could overwrite an opt-out). Full `make check` passed
(2,261 backend, 451 frontend); affected tests rerun after the prompt/case edits.
