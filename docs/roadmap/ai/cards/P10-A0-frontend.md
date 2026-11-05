# P10-A0 part 2 — autonomy screens and notification integration

Status: implemented and locally tested on 2026-11-05 UTC; owner review pending. Tier A / EVENT.
The pre-existing backend work and other working-tree edits were preserved. No commits, deployment,
Docker, installs, external calls or databases other than `smb_a0` were used.

## Screens and behavior

- **AI → Autonomy** (`/ai/autonomy`): owner/admin access via `org.settings.write`. One reorder-action card
  shows Off, Draft (unavailable), Ask, Act and notify, Act, with descriptions; shadow/active switches;
  a validated daily limit; and would-act/acted/fell-back totals for 30 days. Saving uses the loaded version;
  409 opens the existing reload dialog. The server's response replaces the form, including forced shadow
  for a newly created rule. Saving also requires `inventory.proposal.approve`.
- **Pause all AI actions** opens a confirm dialog. A paused business gets a prominent banner and a Resume
  button. The API response supplies the displayed state; unrelated settings are not overwritten.
- **AI → Activity** (`/ai/activity`): permission `approvals.proposal.read`. A cursor-paginated table shows
  business-local time, proposal/action, outcome badge and plain-language reasons, current proposal result,
  rule ID/version and original grantor. Failed execution remains distinct from an earlier Acted evaluation.
  Successful reorder results link to the backend's existing permission-gated draft endpoint; no separate
  draft frontend page was added. The link is hidden without `inventory.reorder.read`.
- **Approvals detail**: policy decisions name the autonomy rule/version and grantor, without inventing a
  human decider. The proposal list has no decider column and required no change.
- **Notifications**: pending proposals retain approval requests, with a shadow explanation for would-act.
  Non-pending policy approvals at `act_notify` send `ai_acted` to the same eligible approvers. `act` and other
  non-pending proposals send no approval request. The notice explains the policy approval and links to the
  proposal for its actual execution result. Bodies now appear in the notification panel.
- Navigation groups AI settings, Autonomy and Activity under AI, filtering each link by its permission.

## Backend/API integration

The notification subscriber uses `approvals.public.proposal_notification_state`; no cross-module internal
imports were introduced. In-app functions moved to `notifications/in_app.py`, preserving service exports
and keeping the service below 400 lines.

Response additions: `DecisionOut.granted_by_name`; Activity's `action_type`, nullable `granted_by` and
`granted_by_name`; `NotificationOut.body`. No database schema/migration changes.

Rule-save audit changes record structured version/level snapshots. Historical grantors and notification
levels resolve using the recorded policy decision, matching rule version or immutable rule-save audit.
Legacy A0 audit summaries are supported by exact-format matching. If history is unavailable, the UI says
so; a newer grantor is never substituted for an older evaluation. Existing seed uses the public rule writer.

Regenerated `backend/openapi.json` with `.venv/bin/python -m app.cli openapi > openapi.json` and regenerated
`frontend/src/api/schema.d.ts` with the existing local `pnpm exec openapi-typescript` generator. No hand edits
to generated types. No AI provider or TypeSafe call is useful for these deterministic forms and stored fields.

## Requirements matched to evidence

| Requirement | Evidence |
|---|---|
| Delayed notification before/after execution; act stays silent; shadow keeps approval; no duplicates; same recipient permissions | Parameterized `test_rule_notification`, including rule edits after evaluation; existing `test_approvers_notified_on_proposal_created` |
| Other non-pending statuses do not request approval | `test_non_pending_human_proposals_do_not_request_approval` (approved/rejected/cancelled/expired/failed/succeeded) |
| Public API boundaries preserved | `test_no_forbidden_imports_in_codebase`, dependency/route/OpenAPI architecture checks |
| Levels, disabled Draft, shadow/active switches, limit, counts and versioned save | `autonomy.test.tsx`: settings card save test and invalid-limit test |
| New-rule server shadow state and 409 reload | `autonomy.test.tsx`: forced-shadow and conflict tests |
| Permission-hidden operator settings and Activity navigation | `autonomy.test.tsx`: operator and missing-proposal-read tests |
| Confirmed pause, cancellation, banner on load/after pause, resume | `autonomy.test.tsx`: pause and paused-load tests |
| Activity outcomes, reasons, business time, links, pagination and empty state | `autonomy.test.tsx`: Activity tests |
| Rule evaluation stays separate from later execution failure; result permissions | `autonomy.test.tsx`: failed-execution test |
| Grantor remains correct after edits, for live/shadow and legacy audit format | `test_activity_keeps_original_grantor_after_rule_edit` (4 cases) |
| Policy detail wording with nullable human decider | `approvals.test.tsx`: policy approval test; backend historical-attribution test |
| Rule explanation visible in notification panel | `notifications.test.tsx` |

## Validation output

Native runtime checks: host arm64; backend Python executable arm64 and `platform.machine() == "arm64"`;
Node from `/opt/homebrew/opt/node@24` reports arm64. Existing environments only; no runtime/package installation.

All database tests used:

```text
TEST_DATABASE_URL=postgresql+psycopg://smb_app:smb_app_dev@localhost:5433/smb_a0
TEST_DATABASE_MIGRATION_URL=postgresql+psycopg://smb_owner:smb_owner_dev@localhost:5433/smb_a0
```

Final requested backend command:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider app/modules/notifications app/modules/approvals
120 passed in 18.98s
```

Affected modules plus selected module-import, dependency-graph, route-permission and OpenAPI checks:

```text
134 passed in 31.90s
```

Final static checks:

```text
.venv/bin/ruff check .
All checks passed!
.venv/bin/ruff format --check .
517 files already formatted
.venv/bin/mypy app
Success: no issues found in 286 source files
```

Frontend full suite and static checks:

```text
pnpm exec vitest run
Test Files  35 passed (35)
Tests       309 passed (309)
Duration    19.37s
pnpm exec tsc --noEmit
(exit 0, but did not check referenced projects; see correction below)
pnpm lint
(exit 0, no diagnostics)
```

Vitest emits the existing jsdom `Window.scrollTo()` warnings; no failed tests. New regression tests initially
identified missing notification-body exposure and a time assertion that omitted LocalTime's MST suffix; both
were corrected. No failing checks remain in the commands above. `git diff --check` passes.

**Not run:** `make check` (a dry run confirms it starts Docker's `db-test`, prohibited for this task),
Docker-dependent deployment checks, live/browser E2E, screenshots, external provider checks or deployment.
The restricted local checks do not claim to verify those conditions. No screenshots were taken; the screens
are described above. An independent second-model/owner review has not been performed in this part.

## Files changed in this part

Backend:

- `backend/app/modules/approvals/{api.py,public.py,reading.py,schemas.py,autonomy_schemas.py,autonomy_service.py,README.md}`
- `backend/app/modules/approvals/autonomy_history.py` (new)
- `backend/app/modules/approvals/tests/test_autonomy.py` (appended grantor-history regression tests)
- `backend/app/modules/notifications/{service.py,schemas.py,README.md}`
- `backend/app/modules/notifications/in_app.py` (new; in-app functions moved here)
- `backend/app/modules/notifications/tests/test_autonomy_notifications.py` (new)
- `backend/openapi.json` (generated)

Frontend:

- `frontend/src/features/ai/{AutonomyPage.tsx,ActivityPage.tsx,autonomy-api.ts,routes.tsx,autonomy.test.tsx}` (new)
- `frontend/src/features/ai/README.md`
- `frontend/src/components/ui/switch.tsx` (new; existing Radix dependency)
- `frontend/src/features/approvals/{pages/ProposalDetailPage.tsx,approvals.test.tsx}`
- `frontend/src/features/notifications/NotificationBell.tsx`
- `frontend/src/features/notifications/notifications.test.tsx` (new)
- `frontend/src/app/{router.tsx,layout.tsx}`
- `frontend/src/lib/permissions.ts`
- `frontend/src/locales/en.json`
- `frontend/src/api/schema.d.ts` (generated)

Documentation:

- `docs/roadmap/ai/contracts.md`
- `docs/roadmap/ai/cards/P10-A0-frontend.md` (this report)
- `docs/roadmap/phases/STATUS.md` (P10-A0 progress only)
- `EVENT_WORK.md` (appended part-2 record)

Spec changes: the explicitly requested part-2 in-app notification/UI behavior and additive response fields
are reflected in the contract. No ADR, dependency, permission definition or execution policy changed.
Open questions: none for this implementation. Owner review and the prohibited/unrun checks remain separate.

## Typecheck follow-up (2026-11-05)

The owner identified seven errors with the actual project command, `pnpm typecheck` (`tsc -b`).
The earlier root-level `tsc --noEmit` did not build the referenced app/test projects and was insufficient
evidence of frontend type safety. Removed the unsupported `exact` option from five role queries; string
accessible-name matching remains exact by default, so the assertions retain their original behavior.
Wrapped pause/resume error handlers as `(error) => toastError(error)` so TanStack's boolean mutation
variable cannot bind to the helper's optional conflict callback. No casts, suppressions or test reductions.

Changed `frontend/src/features/ai/{AutonomyPage.tsx,autonomy.test.tsx}`, this report and `EVENT_WORK.md`.
Follow-up validation:

```text
pnpm typecheck
$ tsc -b
(exit 0, no diagnostics; includes app, tests and Node project)
pnpm lint
$ oxlint --deny-warnings src e2e
(exit 0, no diagnostics)
pnpm exec vitest run src/features/ai src/features/approvals src/features/notifications
Test Files  8 passed (8)
Tests       60 passed (60)
Duration    6.22s
```

`git diff --check` passes. Existing jsdom scrollTo warnings remain. No backend, generated schema,
dependency or TypeScript configuration changes. Full `make check` remains unrun under the no-Docker
restriction; the commands above are the requested follow-up scope.
