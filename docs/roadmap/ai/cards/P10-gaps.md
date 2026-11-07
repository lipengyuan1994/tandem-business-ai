# P10 gaps

Owner-authorized EVENT / Tier A, Codex, 2026-11-07 UTC (owner request dated November 6 locally).
DONE — implementation and permitted verification complete. No release or commit performed.
The one full backend run had a permission-inventory omission, corrected and verified in a focused run;
the full suite was not rerun, respecting the owner’s limit. Browser specifications remain reviewer-run only.

## Changes and decisions

Contracts B1/B2/E1 updated before implementation. No migration, dependency, service, container, prompt,
inference or execution-policy change. TypeSafe considered: these are exact keysets, stored metadata and
permission rules, so deterministic code is appropriate; no hosted judgment was called.

Work uses disjoint bands: emergency hand-overs, open Care cases ordered urgent/high/normal then descending
created_at/UUID, then ordinary chronological work. One-row lookahead spans bands without consuming an
unreturned item. Cursors retain their band; Care's opaque token contains priority plus timestamp/UUID.
Existing emergency/ordinary tokens remain accepted. This is live keyset pagination: consistency assertions
apply to unchanged rows; editing priority or inserting work requires a fresh queue read, not a snapshot claim.
Malformed band cursors use the platform's existing Invalid/422 response.

Care Work rows supply category/version and customer_name. Names come from the existing tenant-scoped CRM
public display-name batch query, only with crm.customer.read, and never include contacts/notes. Unknown IDs
return null. Screens render row category/name directly and key the case expansion by version; detail GET still
supplies the latest version for a resolve decision. No client sort or loaded-rows sorting note remains.

Chain depth is max recorded chain_depth on runs/proposals, default 0 with no recorded run/proposal depth.
Root events can be depth 0; the existing agent-run root/admission convention of depth 1 remains unchanged.
Team activity retains recorded event/job/run depths and the owning proposal's depth for derived evidence.
The chain screen reads “Depth 2 of 6”; no depth is inferred from number of timeline rows.

AgentOut returns stable registry triggers and switchable. Existing namespaced event/job codes are preserved;
assistant_request is added to each agent because all seven support targeted chat. All seven permit enabled
PATCH, so switchable is true for each. The frontend hides an explicitly false switch, translates trigger chips,
and removes the legacy Coming-soon reason heuristic.

GET /ai/agents/addressable requires ai.assistant.use (the copilot permission), returns only
items[{key,name,enabled}], manager_enabled and manager_reason. Disabled agents remain in discovery but are
not selectable. Disabled Manager reason is ai.agent_disabled; otherwise null. The service reads settings and
registry metadata only. Existing Team, Activity, Chain and Work read permissions are unchanged. Assistant-only
users use this endpoint for @ and Manager-off; no Team projection is fetched for discovery.

## Files

- Backend AI: agents.py, work_queue_api.py, customer_care/api.py, team_schemas.py, team_service.py,
  team_activity.py, team_api.py; approvals/team.py adds depth through its existing public Team projection.
- Backend tests: tests/integration/test_p10_gaps.py; app/modules/ai/tests/test_team_controls.py updates the
  registry's exact expected trigger set; test_safety_pass_boundaries.py includes the new route in the exhaustive
  permission sweep and explicitly verifies assistant-only access.
- Generated: backend/openapi.json and frontend/src/api/schema.d.ts via make gen-api.
- Frontend AI: WorkQueuePage, ChainPage, AgentCard, AgentMention, AssistantPanel, team-api, team-text;
  team-testing, team.test, team-assistant.test, customer-care.test; locales/en.json and test/app.tsx.
- Browser specs: frontend/e2e/{self-service,widget,dental}.e2e.ts; support/accounts.ts, global-setup.ts and
  auth.setup.ts add ellery.dental@example.com through app.cli dev-account, following Casey's setup.
- Docs: contracts, this card, HANDOFF, STATUS, backend/frontend AI and approvals README cards, the B2/B3 screen cards’ follow-up links and EVENT_WORK.

## Requirement evidence

| Requirement | Evidence and conditions |
|---|---|
| Three bands, no skips/repeats, priority/newest/UUID ties | test_three_bands_keysets_ties_and_boundaries: sizes 1/2/50; size 50 traverses 54 Care cases and both adjacent bands on smb_gaps |
| Care category/version/display-only name and permission/tenant isolation | Same API test asserts new fields, with/without CRM read, no email exposure, foreign tenant empty |
| Root-only, hand-off and stopped chain depths | test_measured_depth_from_runs_and_proposals_not_timeline_length, stopped false/true; run and proposal depths, event depth excluded from max |
| Every agent's triggers/switchability | test_capabilities_match_every_registry_agent_and_enabled_patch; all seven exact lists and accepted enabled PATCHes |
| Minimal assistant-only discovery and denial/isolation | test_addressable_minimal_assistant_only_permissions_and_tenant_settings: assistant-only 200/exact fields, read-only/no permissions 403, foreign Manager default isolated; existing protected endpoints still 403 |
| Frontend server order across loaded pages | Customer Care MSW tests deliberately return different priorities in server order and assert exact order/cursor, no extra CRM fetch, no loaded-row note |
| Measured depth, chips, hidden unsupported switch | Team MSW tests assert Depth 2 of 6, plain trigger chips, switchable false hiding switch while retaining limit editing |
| Assistant-only @/target/retry/Manager-off | Team assistant MSW scenarios use only ai.assistant.use and minimal discovery; full Team fetch throws; exact target and same-key retry asserted |
| Self-service e2e | Written/static checked only: seeded upcoming cycle customer, UI enable/save, simulated email link, clean recipient context, fee acknowledgment/cancel and staff notification |
| Widget e2e | Written/static checked only: real Node HTTP hosts 5555/5556, exact requested embed script, keyboard open through closed shadow root, iframe message/sessionStorage resume, required CSP console refusal and absent rendered chat |
| Dental e2e | Written/static checked only: fixture instructions/banner, exact synchronous system reply, staff API emergency/human state and no assistant message, staff Inbox |

## Verification

Existing native arm64 uv/Python/Node and psycopg compiled extensions verified; native Rolldown, Tailwind and
Oxlint bindings verified with file. No runtime creation, installs, downloads, external calls, Docker, commits
or deployment. Frontend .bin shims had stale removed-worktree paths; repaired local shims only and used
pnpm --config.verifyDepsBeforeRun=false --config.offline=true to avoid automatic installs.

- Initial business-rule test reproduced the pre-fix chronological Care ordering failure.
- First focused backend regression pass: 48 passed in 23.03 s.
- Expanded focused backend pass: 42 passed in 22.18 s (includes 54-case pagination).
- Full backend suite, invoked exactly once: **2,397 passed, 1 failed in 2,097.87 s; 96% coverage**.
  Failure: test_every_ai_endpoint_requires_its_permission had 26 inventory entries for 27 routes. Added the
  new addressable endpoint to the permission sweep and asserted assistant-only 200; no assertion was removed
  or weakened. No production code changed after this run. The entire safety boundary file plus all gap
  tests then passed: **30 passed in 28.62 s**. No second full backend run was performed.
- Final full frontend suite: **539 passed / 52 files, 27.46 s**, after all frontend changes.
  Existing jsdom scrollTo warnings were present; no tests skipped or weakened.
- make gen-api: passed; generated schema and types included.
- Ruff/format: passed, 713 files formatted; mypy: passed, 375 source files.
- Frontend typecheck/lint: passed, including all three e2e specs.
- Final smb_gaps readback: **20261106_1500_self_service (head)**. No migration added.
- Fixtures: 283 synthetic rows in 33 tables. git diff --check passed.

The initial aggregate invocation ran the full backend suite once and stopped at its single inventory failure:

```sh
UV_NO_SYNC=1 UV_OFFLINE=1 UV_CACHE_DIR=/private/tmp/p10-gaps-uv-cache \
PATH=/private/tmp/p10-gaps-bin:$PATH \
DEPLOY_CHECK_OFFLINE=1 DEPLOY_CHECK_NO_DOWNLOADS=1 \
TEST_DATABASE_URL=postgresql+psycopg://smb_app:smb_app_dev@localhost:5433/smb_gaps \
TEST_DATABASE_MIGRATION_URL=postgresql+psycopg://smb_owner:smb_owner_dev@localhost:5433/smb_gaps \
make -o test-db check
```

The temporary pnpm wrapper only adds offline/no-verification flags to the installed native command.
-o test-db bypasses Docker startup; pytest still migrates the isolated database to head. After the
permission-inventory correction, the same environment ran **make -o backend-test -o frontend-test check**.
This is a scoped make check: both test targets were excluded because the separately recorded suites had
already run. Its final summary:

```text
All checks passed!
713 files already formatted
Success: no issues found in 375 source files
Ran 108 tests in 80.539s
OK (skipped=19)
deploy checks: ALL PASSED
backend licences OK
frontend licences OK
no leaks found
make check: ALL PASSED
```

Deployment offline mode explicitly skips Docker Compose resolution, Caddy runtime, Terraform provider and
cloud-init schema checks. The unrestricted final make check is not claimed green. Logs:
/private/tmp/p10-gaps-make-check.log (full run with inventory failure),
/private/tmp/p10-gaps-backend-post-full-focused.log (30 passing after correction),
/private/tmp/p10-gaps-frontend-full.log (539 passing), /private/tmp/p10-gaps-make-scoped-check.log.

## Not done / reviewer conditions

No Playwright spec was run: global setup needs Docker and reseeds the shared development stack. Browser layout,
actual iframe CSP behavior, sessionStorage reload, self-service email UI and dental login/emergency flow remain
reviewer verification. The widget spec requires the requested https://localhost:8443 dev host and free local ports
5555/5556; the self-service spec explicitly fails if fixture dates contain no upcoming confirmed cycle booking.
Settings touched by the specs are restored using the latest version; the intentionally cancelled synthetic
booking is restored by the next global reseed. No real e-mail delivery, external model call or release tested.
The original task did not include independent reviewer/second-model review. The later reviewer’s browser report and frontend follow-up are recorded below.

## Reviewer widget follow-up — 2026-11-07

DONE — frontend implementation, requested unit/static/build checks and reviewer-run e2e updates complete.
Post-fix real-browser geometry and CORS/CSP acceptance remain reviewer-run; no Playwright was executed here.

**Reviewer report (not independently rerun here):** real cross-site Chromium on the dev stack successfully
sent an iframe chat message, retained the conversation on host reload, and refused the frame from :5556
while :5555 was allowed. The reviewer found a default launcher/empty panel on the refused host and a
clipped chat heading under the close header. Those are the two defects addressed in this follow-up.

Contracts updated first. Files: frontend/public/embed.js; src/widget/{Widget,EmbedChat}.tsx,
{embed-loader,EmbedChat}.test.tsx and README; frontend/e2e/widget.e2e.ts; this card, contracts, STATUS,
HANDOFF and EVENT_WORK. No backend edits or backend tests in this follow-up. Existing uncommitted API work
from the original task is preserved. No new dependencies, installs, downloads, external calls or Docker.

- Loader v1.0.1 fetches the business config before creating any shadow host, launcher, iframe or panel.
  Only HTTP success, parsed JSON and a nonblank string label of at most 80 characters permit mounting.
  Labels are trimmed. CORS/network/HTTP/JSON/invalid-label failures are caught silently; no default launcher.
  The root remains closed, now tested in its actual closed mode via a captured reference.
- Header and iframe occupy a column flex layout. The header cannot shrink; the iframe fills the remaining
  space with min-height zero. The close button uses border-box sizing. This removes the fixed subtraction
  of an assumed header height. The embedded heading is programmatically focusable and receives focus with
  preventScroll; opening no longer scrolls to the form's first input.
- The browser spec retains message sending, sessionStorage resume and setting restoration. It now runs at
  1280 and 375 px widths with iframe/header/heading bounds and no initial scroll assertions. The disallowed
  host waits for its actual config request refusal and asserts unchanged host DOM, no launcher and no child
  frame. It then injects a separate direct iframe and requires a CSP refusal and absent rendered chat.
- TypeSafe considered: exact DOM admission, CSS layout and trusted message checks use ordinary code;
  no hosted semantic judgment is needed or called.

Verification: before the loader fix, its new tests reported 12 failures reproducing pending/invalid-config
mounting and layout rules. Final focused widget run: 60 passed, 5 files, 2.43 s. They cover CORS/network,
403/500, malformed JSON, null/missing/non-string/empty/whitespace/oversize labels, valid trimmed/80-character
labels, closed-root origin/source checks, flex layout rules, preventScroll heading focus and gzip budget.
jsdom verifies layout declarations, not painted pixel bounds. The desktop/375 px browser geometry assertions
have been written and statically checked but remain unrun by explicit owner instruction.

Final follow-up verification:

- Focused widget suite: **60 passed / 5 files, 2.43 s**.
- Final full frontend Vitest: **554 passed / 52 files, 24.08 s**. No skipped/disabled tests; existing jsdom
  scrollTo warnings remain. Frontend typecheck and lint passed, including desktop/375 px e2e geometry code.
- **pnpm build passed** (tsc -b and Vite; Vite 679 ms). Built embed.js is byte-identical to public/embed.js:
  **4,365 bytes raw / 1,714 bytes gzip**, below **5,000 bytes**. Initial widget static transitive JS is
  **76,875 bytes gzip**, below **80,000 bytes**. Measured with native arm64 Node gzipSync at default compression,
  each JS file separately; excludes CSS, source maps and route-lazy Bookings/SupplierQuote chunks.
  Static graph: widget-Cx2Bflgr.js 5,397 B; preload-helper-D3wrnp6O.js 3,515 B;
  react-runtime-DCTAcUCH.js 67,595 B; rolldown-runtime-WkwZ5CU-.js 368 B.
- Frontend-scoped make check passed: temporary /private/tmp/widget-review-check.mk overrides lint/typecheck
  with frontend commands; make -f Makefile -f /private/tmp/widget-review-check.mk -o backend-test
  -o deploy-check -o licenses check runs fixtures, frontend lint/typecheck/full Vitest and secrets.
  Backend tests/lint/types, deployment and licence checks are excluded in this follow-up. Unrestricted
  make check is not claimed. No backend production or test source was changed in this follow-up.
- Native Node architecture: arm64; reused the installed dependency tree with the existing offline pnpm wrapper.
  The source directory and copied build artifact were both checked for the loader gzip budget.
- git diff --check and the final secret scan passed. No installs, downloads, external calls, Docker, commits
  or deployment. No browser automation was run.

Logs/artifacts: /private/tmp/widget-review-red.log (12 pre-fix failures),
/private/tmp/widget-review-focused-final.log, /private/tmp/widget-review-make-check.log,
/private/tmp/widget-review-build.log, /private/tmp/widget-review-sizes.json.

| Follow-up requirement | Evidence / remaining condition |
|---|---|
| No launcher/panel before a successful valid config; silent failure; closed root | Shipped-script unit tests verify untouched host, no attachShadow call and no added console logs for pending/refused/invalid config; actual closed mode and 80-character/trimmed labels pass |
| Header never covers the iframe or scrolls away the heading | Computed CSS tests verify header reservation and iframe flex shrink; embedded heading focus with preventScroll passes. Actual painted geometry at 1280/375 px is asserted in the updated spec, not run here |
| Loader at most 5 KB gzip | Built artifact 1,714 B gzip; exact equality with source; compression unit test and independent Node build measurement pass |
| Disallowed host no launcher plus independent CSP refusal; preserve send/reload | Updated spec waits for failed CORS config, asserts no mounted host/launcher/frame, then directly injects an iframe and requires CSP refusal; message/sessionStorage/restore steps retained at both widths. Reviewer executes it |
