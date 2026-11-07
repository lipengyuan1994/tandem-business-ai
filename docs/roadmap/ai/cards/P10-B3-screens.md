# P10-B3 screens — AI Team

2026-11-06 · EVENT · Codex. **DONE — frontend against available API; explicit gaps below.** Frontend implemented against the generated P10-B1 schema; backend untouched.
Verification is local/MSW, not a live backend or browser acceptance run. API limitations below remain explicit.

## Result

- `/ai/team` is the first AI navigation item. Cards show registry identity/job, five plain-language statuses,
  backend reasons, action autonomy links, today's work, USD cost/limit, waiting count/oldest age, and LocalTime.
- `/ai/team/:key` reuses the card and adds cursor activity, automatic scroll loading plus a Load more fallback,
  kind icons, execution status, delivery badges, cost, reasons, proposal/result links and View chain.
- `/ai/chains/:id` preserves server step order, labels agent transitions, shows model calls/actions/USD versus
  ceilings, the depth ceiling and a stopped explanation. Current depth stays explicitly unavailable.
- The assistant offers enabled agents after `@`, with name filtering, keyboard-focusable buttons, a removable
  destination chip and optional `agent` in the SSE POST. Retry preserves that agent and idempotency key.
  No destination keeps the existing Manager route. Known Manager-off replaces the input with its explanation.
- Activity can select an agent timeline, with selection in the URL. The existing all-agent rule Activity remains;
  agent names appear when the response supplies `agent`. Autonomy accepts `?action=`. Work queue accepts
  `?agent=` and filters loaded cursor pages by every action in that agent's autonomy list. Load more remains
  reachable even when the first page contains no matches.

## Permissions, edits and decisions

Read is `approvals.proposal.read`; write controls require `org.settings.write`. Protected pages do not fetch
without read permission. Read-only cards contain neither switches nor limit inputs. Limits reuse the USD money
parser: non-negative, at most two decimals, safe magnitude; blank sends null. Insignificant backend decimal zeros
are normalized without rounding a sub-cent limit. Disabling requires “Its queued work goes back to people”.
Every PATCH carries the displayed version. A 409 reloads Team and retains “Someone else changed this agent;
check and try again”; the next edit uses the refreshed version. Other failures remain retryable.

Assistant-chosen conservative default, pending the optional clarification sent in chat: Customer Care's switch
is disabled while its backend reason says no triggers/coming soon. It becomes available when that reservation
reason clears. This is not an independently verified `can_enable` capability: the generated model has no such
field. The backend remains authoritative for PATCH acceptance. No new model, dependency, service or API call
outside the existing product endpoints was added. TypeSafe considered; exact backend states and permissions
need deterministic rendering, not semantic judgments. No TypeSafe network request was made.

## Files

New, under `frontend/src/features/ai/`:

- `team-api.ts`, `team-text.ts`: generated API hooks and shared formatting/reason text.
- `AgentCard.tsx`, `TeamPage.tsx`, `TeamActivity.tsx`, `ChainPage.tsx`, `AgentMention.tsx`: product screens.
- `team.test.tsx`, `team-assistant.test.tsx`, `team-testing.ts`: MSW scenarios and synthetic fixtures.

Modified:

- `frontend/src/features/ai/{routes.tsx,api.ts,AssistantPanel.tsx,ActivityPage.tsx,AutonomyPage.tsx,WorkQueuePage.tsx}`.
- `frontend/src/{app/router.tsx,lib/permissions.ts,locales/en.json,test/app.tsx}`. Existing tests get an empty Team
  discovery response by default; dedicated scenarios override it.
- `frontend/src/features/ai/README.md` (own table row), this card, own STATUS row and own EVENT_WORK entry. The supplied `schema.d.ts` was only read, never edited.

## Requirement evidence

| Requested requirement | Evidence / conditions |
|---|---|
| All seven cards, all statuses, local-model/Coming-soon reasons, work/cost/waiting/time | Team list MSW test; Denver time asserted as MST; no live endpoint run |
| Off confirmation, versioned PATCH, on without confirmation | Switch test asserts cancel sends nothing and exact version progression 3 → 4 |
| 409 reload/message and next version | Conflict test verifies refreshed limit and second PATCH version |
| Limit validation/null/read-only controls | Five invalid-input cases; blank/zero/two-place payload cases; read-only card assertions |
| Read gates for list/detail/chain | Three no-read cases assert protected requests are never made |
| Cursor timeline and four delivery states | Detail test verifies opaque cursor, scheduled/delivered/failed/stopped badges and evidence links |
| Infinite scroll | Mock IntersectionObserver enters view and loads the next cursor without clicking |
| Chain order/handoffs/calls/actions/cost/stop | Two chain scenarios assert ceilings, order, hand-off and budget/transition explanations |
| Depth usage | Partial: API exposes only max_depth; current depth cannot be verified or displayed |
| Reserved Customer Care | Two MSW cases: no-triggers reason disables; cleared reason enables. No backend capability flag available |
| @ chip, enabled choices, target body/retry, default Manager, Manager off | Three assistant MSW tests assert destination, omitted field, same-key retry and absent composer |
| Activity agent filter and supplied agent names | Selected Team timeline plus optional all-agent metadata cases |
| Autonomy/Work queue links work | Filtered Autonomy test and empty-first-page Work queue cursor test |
| Bundle growth ≤ approximately 15 KB gzip | Before/after production builds: total JS+CSS+HTML gzip 403,757 → 413,536 bytes; +9,779 bytes |

## Verification

Native arm64 Node, Homebrew uv/Python and the existing backend interpreter verified. Existing dependencies only.
No installs, external network calls, commits, cloud actions or deployment. No backend file edits.

- `pnpm typecheck`: passed (`tsc -b`).
- `pnpm lint`: passed (`oxlint --deny-warnings src e2e`).
- Final `pnpm exec vitest run`: **42 files / 451 tests passed in 19.04 s**.
- Final focused run (Team, Team assistant, existing Autonomy and assistant): 4 files / 50 tests passed in 5.16 s;
  includes all 27 new cases. The complete suite was then rerun on the final code.
- `pnpm build`: passed; final `vite build --outDir /private/tmp/p10-b3-after` also passed after the last
  Activity label fallback change. Gzip comparison sums individual `.js`, `.css`, `.html` assets (maps excluded),
  including shared chunks and the widget, using Python gzip with a fixed timestamp. Baseline is the starting
  working tree with the supplied generated schema; unrelated pre-existing work was preserved.
- `git diff --check`: passed for frontend and own shared-doc edits.

Unrestricted `make check` was invoked with uv offline/no-sync and deployment offline settings, then deliberately
interrupted before its Docker/database prerequisite. Backend ruff/format and mypy passed; it was interrupted at
frontend typecheck, which passed separately. To honor no-network and concurrent-backend constraints, ran:

```sh
UV_NO_SYNC=1 UV_OFFLINE=1 UV_CACHE_DIR=/private/tmp/p10-b3-uv-cache \
DEPLOY_CHECK_OFFLINE=1 DEPLOY_CHECK_NO_DOWNLOADS=1 \
make -o backend-test -o frontend-test check
```

```text
All checks passed!
644 files already formatted
Success: no issues found in 341 source files
Ran 106 tests in 80.685s
OK (skipped=19)
deploy checks: ALL PASSED
backend licences OK
frontend licences OK
no leaks found
make check: ALL PASSED
```

This is a **scoped** check: backend tests/database setup and the separately-run frontend suite were excluded.
Deployment offline mode skips Docker Compose resolution, live Caddy, Terraform providers and cloud-init schema;
19 deployment regression cases also self-skipped. Fixtures passed (149 synthetic rows / 30 tables).

## Not done / API limits

Follow-up: [P10-gaps](P10-gaps.md) implements the measured depth, trigger/switch capabilities and
assistant-only discovery described below. That card tracks current verification; this section records
the original screen task's API boundary and test conditions.

- No measured chain-depth usage exists in `ChainOut`/`TeamActivityItem`; depth is shown as a ceiling and unknown
  usage, not reconstructed from timeline length. Backend additions are outside this task.
- No trigger list or explicit switch capability exists in `AgentOut`; the reserved Customer Care decision above
  depends on its backend reason. A backend that allows targeted chat while reporting coming soon would need a
  capability field or a clarified UI policy. No claim of such a backend acceptance test is made.
- Team discovery requires proposal-read. An assistant-only user without that permission cannot discover agents
  or independently read Manager-off state; existing chat remains subject to backend admission. Manager-off UI is
  tested under both assistant-use and proposal-read, as supplied by the scenario's session. This is an API scope
  limitation, not permission widening in the frontend.
- No real-browser layout/a11y run, live backend integration, provider/e-mail delivery, backend test suite,
  unrestricted make check, deployment or release. MSW tests prove frontend behavior under their supplied data.
