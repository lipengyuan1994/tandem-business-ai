# P10-B2 screens — Customer Care

Tier B / EVENT, Codex, 2026-11-06. Frontend implementation and offline verification complete;
**global priority across unloaded Work pages needs backend support** (see API limits).
Owner reports backend released in 0.1.8; no hosted state was independently checked in this task.
Worktree `p10-b2-screens`, starting HEAD and comparison `main`: `d0516f5`.

## Behavior and decisions

- Customer Care uses the normal Team switch, loaded version and off confirmation. Removed its special
  switch disablement based on old “Coming soon” prose. Backend status/reason remain visible.
- Team/detail, Activity and Work translate Care reason codes. Covers every fixed activity/eligibility
  reason found in the Care backend, including no consent, dispute, quiet hours, duplicate, preference,
  erased/inactive customer, future appointment, missing language/source, changed facts, recall off/not due,
  minimal template and human-only replies. Also covers requested `recall_facts_changed` and invoice evidence;
  unknown Care codes use generic text, never raw codes.
- Care settings live on `/ai/team/customer_care`, inside `org.settings.write`. Delay is **0–365** days,
  matching `org/schemas.py` (0 off); hours are whole 0–23, equal means off, with the session's business zone.
  Warm/formal/concise tone, unrelated settings preserved, versioned PATCH and explicit 409 reload.
- No existing frontend public-profile editor was found. Added review/booking fields to Business settings
  (`org.business.write`, existing Business endpoint). Empty clears a link. Absolute https only, max 500,
  no embedded login details; preserves other profile keys including `avoid_medical_details`. This is
  intentionally stricter than the backend's HTTP(S) allowance, as requested. Versioned save/409 reload.
- Work includes Care cases in its agent filter despite their missing `action_type`. Loaded cases sort
  urgent/high/normal, before other work. Expand “View case” for GET-backed category, status, customer,
  Inbox thread, appointment/invoice links, current reply proposal and resolution. Unknown customers link
  to their thread for identification. Names resolve only with CRM read permission; otherwise a customer
  ID link remains. Resolve requires `conversations.proposal.approve`, confirms that this case's hold is
  lifted, POSTs its current version, refreshes Work/Team, and requires reload/reconfirmation after 409.
- Work proposals and case suggested replies show kind, customer, subject/body, earliest send time via
  `LocalTime`, and exposed payload facts (appointment/service/date/invoice/source message/recall month).
  Approvals detail uses the same preview. Complaint replies say “Needs a person”. The existing revision,
  approve/reject and permission behavior is reused; the new editor changes subject/body while preserving
  original facts, source generation and send time. No new sending path or endpoint.
- CRM create/edit/detail shows E-mail / Web chat / Don't contact. Editing compares against the opening
  preference and omits it when unchanged, including an existing opt-out. Current staff/import email
  service consent is identified as allowing proactive service messages; form service-update evidence is
  “This request only”. Historical form marketing grants keep their distinct meaning. Consent/contact/
  complaint constraints are explained without presenting consent alone as permission to deliver.
- TypeSafe was considered; this UI maps fixed API states and exact validation rules. No hosted judgment,
  SDK, dependency, model integration or live documentation call was needed or made.

## Files

New frontend files:

- `features/ai/CustomerCareSettings.tsx`, `CustomerCareCase.tsx`, `CustomerCareProposal.tsx`,
  `customer-care-api.ts`, `customer-care.test.tsx`, `customer-care-proposals.test.tsx`.
- `features/approvals/components/forms/CustomerCareForm.tsx`.
- `features/org/care-profile.test.tsx`.

Updated under `frontend/src/`:

- AI `AgentCard.tsx`, `TeamPage.tsx`, `WorkQueuePage.tsx`, `ActivityPage.tsx`, `team-text.ts`,
  `team.test.tsx`, `README.md`.
- Approvals `components/forms/index.ts`, `pages/ProposalDetailPage.tsx`.
- CRM `components/CustomerDialog.tsx`, `components/ConsentPanel.tsx`, `pages/CustomerDetailPage.tsx`,
  `crm.test.tsx`.
- Org `pages/BusinessSettingsPage.tsx`; `locales/en.json`.

Documentation: this card, own STATUS row and own EVENT_WORK section. No backend, generated schema,
service editor, dependency manifest or lockfile changes. No commits, cloud actions or deployment.

## Requirement evidence

All behavioral checks below run through the real frontend routes and MSW, with synthetic data only.

| Requirement | Evidence |
|---|---|
| Team switch/version/off confirmation; Care reasons and unknown fallback | `customer-care.test.tsx`: switch test, detail reason tests; existing `team.test.tsx` regression |
| Delay off/365 limit/invalid and fractional input; hours 0–23/equal off, tone, business zone | `customer-care.test.tsx`: save, boundary and seven invalid-input cases |
| Settings conflict reload uses refreshed version | `customer-care.test.tsx`: versions 4 → 6 |
| Optional https links, clear, preserve profile, conflict and permissions | `care-profile.test.tsx`: nine cases; versions 3 → 5 on reload |
| Known/unknown customer, priority, category and appointment/invoice links | `customer-care.test.tsx`: priority rows, unknown-case details/Inbox and related links |
| Resolve current version, human confirmation, hold explanation and conflict | `customer-care.test.tsx`: version 7 POST; conflict reload/reconfirmation 7 → 9 |
| CRM omission preserves opt-out; changed preference sent | `crm.test.tsx`: unchanged `none` omits field; changed `web_chat` included |
| Staff/import consent vs request-scoped form evidence | `crm.test.tsx`: source parametrization and form-marketing distinction |
| All four proposal kinds, customer, subject/body, local send time and facts | `customer-care-proposals.test.tsx`: four kind cases, Work and case previews |
| Complaint needs person; exact approve/reject revision and safe edit | `customer-care-proposals.test.tsx`: decision and preserved-payload editor cases |
| Permission gates | Settings/switch hidden; Resolve hidden; public links disabled; proposal controls obey server `can_decide` |

## Verification

Final native/offline frontend verification (2026-11-06):

- `pnpm typecheck`: passed (`tsc -b`).
- `pnpm lint`: passed (`oxlint --deny-warnings src e2e`).
- `pnpm exec vitest run`: **494 passed, 45 files, 22.12 s**. All 43 added cases pass.
  jsdom logs its existing unimplemented `scrollTo` warning; no test was skipped or weakened.
- `pnpm build`: passed (`tsc -b && vite build`, final Vite phase 610 ms).
- Bundle versus a separate `git archive main` build at `d0516f5`, using the same existing dependencies:
  **413,536 → 418,872 bytes gzip**, **+5,336 B** (5.21 KiB), below ~12 KB. Sum of individually gzipped
  `.js`, `.css`, `.html` assets, fixed gzip timestamp; 98 assets in each build, source maps excluded.
- `git diff --check`: passed. Backend/generated-schema/catalog diff: empty.

All pnpm commands use `--config.verifyDepsBeforeRun=false --config.offline=true` (see incident below).
The baseline and task have separate writable caches. Full logs reside locally at
`/private/tmp/p10-b2-final-check.log`, `/private/tmp/p10-b2-final-build.log`,
`/private/tmp/p10-b2-main-build-verified.log`, `/private/tmp/p10-b2-bundle.json`.

**Scoped** `make check`, including the final full frontend suite:

```sh
make -f Makefile -f /private/tmp/p10-b2-check.mk -o backend-test -o deploy-check check
```

The temporary makefile overrides `lint`, `typecheck`, `frontend-test` and `licenses` with frontend-only
commands and the offline/no-verification pnpm flags. Fixtures and secrets use the repository targets.
No tracked Makefile changes. Backend tests/lint/types/licenses, database and deployment checks were
excluded; **unrestricted `make check` was not run**.

```text
OK — 151 rows in 30 tables
$ oxlint --deny-warnings src e2e
$ tsc -b
Test Files  45 passed (45)
Tests       494 passed (494)
frontend licences OK
no leaks found
make check: ALL PASSED
```

## API limits and untested conditions

Follow-up: [P10-gaps](P10-gaps.md) implements server priority pagination and Work row category/version/
permission-gated display names. That card tracks current verification; the limits below record the
original screen task's API boundary and test conditions.

- Work's existing endpoint uses chronological cursor pagination and exposes no priority query/order parameter.
  Frontend sorts all **loaded** cases; a visible note and Load more retain that distinction. Global priority
  across unloaded pages is **not implemented or verified**. A server-side priority pagination contract is
  the follow-up; no endpoint was invented. MSW verifies priority order for the available rows.
- Work rows omit category/version; actual case GET supplies them after expansion. Customer names are not
  in CaseOut/WorkItem; permitted CRM GET resolves them, with ID fallback. The proposal payload exposes IDs,
  dates and original recall month, not the full writer input; only available facts are displayed.
- No live backend, real browser/layout/accessibility, e2e, email/provider delivery or deployment verification.
  Backend suite, database setup and deployment checks are excluded from the frontend scoped check.
- Test-first attempt was blocked by missing local dependencies; no business-rule red/green claim is made.
  Initial focused tests found duplicate-link test queries and native-vs-custom URL validation differences;
  corrected the query scope and use explicit schema-based URL error messages. Later review narrowed
  request-only history labels to service consent, preserving form marketing consent semantics.
- **Tooling incident:** the first pnpm commands unexpectedly invoked its automatic pre-script install and
  attempted registry requests, which failed DNS. Stopped the command and moved its incomplete dependency/store
  directories to `/private/tmp`. Subsequent commands disable verification and force offline mode:
  `pnpm --config.verifyDepsBeforeRun=false --config.offline=true …`. No successful dependency installation or
  network response is claimed. Final checks reuse the existing dependency tree through local symlinks,
  with writable build/test caches confined to this worktree and `/private/tmp`. No other checkout was edited.
  Node, Python and Rolldown/Tailwind/Oxlint compiled bindings were verified native arm64.
