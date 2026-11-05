# P10-A1 screens — explicit action confirmation and delivery supervision

Status: frontend implemented; local validation below. Full P10-A1 remains in progress for supervisor integration.
Tier A / EVENT, 2026-11-05. Contract: [P10-A1](../contracts.md#p10-a1-confirm-in-chat-po-delivery-send-delay).
Backend handoff: [P10-A1-backend](P10-A1-backend.md).

## Behavior

- Saved thread proposals render action cards with the approvals before/after preview, execution badge,
  advisory eligibility and plain refusal reasons. Confirm sends only the loaded revision id and hash,
  updates the returned card, refreshes thread/proposals/queue and polls active execution/delivery.
  A 409 refreshes without retrying the mutation. Edit links to the editable proposal detail.
  Not now collapses locally and can be reopened. Chat explicitly says typed “yes” does not approve.
- The SSE proposal event has only id/title: while streaming it remains a review link, then the saved
  thread response supplies the full card. Legacy responses without card metadata retain review links.
  Neither fallback provides confirmation without a revision/hash.
- Results use whitelisted PO/draft references and permission-aware links. The card says created,
  approved or sent according to its action, separately from its delivery badge. The card DTO has UUID
  references, so it links to the PO detail rather than inventing a human PO number.
- Cards, Activity and proposal detail show none/scheduled/sending/delivered/failed/unknown/stopped.
  Unknown tells staff to check with the recipient. Proposal detail also retains simulated-channel wording.
  Scheduled cards/Activity fetch the scoped proposal outbox for individual ids and `not_before` dates;
  proposal detail already has that read. Detail continues polling through scheduled/sending delivery.
- Shared countdown/Stop controls appear in cards, Activity, proposal detail and Work queue. Stop needs
  a dialog, posts once, refreshes operational queries, and handles cancelled versus too-late outcomes.
  Mutation-level success/error notices survive refreshes that remove the original row/component.
  A countdown reaching zero does not claim that sending began; Stop authority/state stays server-checked.
- Work queue shows pending roles, age, expiry and proposal links; queued mail uses `stop_until` for
  countdown (including retries); supplier invitations show reply-by as a calendar date and request link.
  Exceptions shows execution failures in plain language, failed/unknown delivery, recent fallbacks and
  current pause with an Autonomy link. Both use cursor pages and proposal-read gates/navigation.
  No Exceptions count badge: the response exposes no total.
- Autonomy edits the exposed business send delay: 0–15 minutes, default 2, persisted as 0–900 seconds.
  Saving preserves other settings and loaded version; a version conflict offers reload. Existing valid
  second-resolution values are displayed in minutes without forcing whole-minute rounding.
- Regenerated backend types also required updating Activity/decision/supplier fixtures and explicitly
  preserving supplier `active` on form submissions (default true for creation). No supplier activation
  controls were added. No backend or generated schema edits, new dependencies, installs or network calls.

## Requirement evidence

All UI evidence is React Testing Library + Vitest + MSW, using the application route tree, session
permissions and generated request types. It does not verify a running backend, browser layout or SMTP.

| Requirement | Evidence in `frontend/src/features/ai/actions.test.tsx` |
|---|---|
| Preview/status/Edit, exact revision/hash + CSRF, executing → result and thread refresh | `renders preview, revision-bound confirm, executing and result and refreshes the thread` |
| Second person/daily limit/permission/changed/expired/not pending + business refusals | parameterized `explains refusal` cases |
| Collapse without mutation; typed yes never confirms | `Not now collapses locally...`; `does not turn typed yes into confirmation` |
| 409 reload without automatic confirmation retry | `reloads after a 409 without retrying confirmation` |
| Seven delivery states independent of execution; sent PO still scheduled | parameterized delivery-state cases; `links the sent purchase order while showing delivery is still scheduled` |
| Stop dialog, cancel, success, too late | parameterized `confirms Stop before posting...` |
| Stop across Activity/detail/queue; race notice survives sending refresh | `shows scheduled delivery and Stop on...`; `stops queued mail from Work queue...`; `keeps the too-late notice visible...` |
| Queue roles/age/expiry/reply-by/links/pagination; exception categories/fallback/pause | operational queue list cases |
| Both empty states and hidden navigation/no request without permission | parameterized queue empty/permission cases |
| Delay default/range/seconds/version/settings preservation; conflict + zero | delay integration cases |

Existing assistant, autonomy, approvals, purchasing, notifications and navigation suites also run unchanged
apart from the generated-field fixture additions noted above. No assertion was removed or loosened.

## Validation

`frontend/` final checks (2026-11-05):

```text
pnpm typecheck
$ tsc -b
exit 0

pnpm lint
$ oxlint --deny-warnings src e2e
exit 0

pnpm exec vitest run
Test Files  36 passed (36)
     Tests  347 passed (347)
  Duration  17.41s
exit 0
```

The new `actions.test.tsx` contributes **38 passing tests**; the existing 309 also pass.
Vitest prints jsdom’s existing `Window.scrollTo` not-implemented notices; no unhandled MSW requests
or failed tests appear in the final run. Native Node, uv and backend Python were verified arm64;
no runtime was created or installed. `git diff --check` passes.

`make -n check` inspected successfully. **Full `make check` was not run:** its dependency chain starts the
Docker database, executes backend DB/SMTP tests and deployment checks that can require network access,
which conflicts with this task's explicit no-network/no-installs constraint. This is not equivalent to
passing `make check`. No backend tests were run for this frontend-only change.

Not verified: running-backend integration, real browser/responsive layout, real or simulated provider
execution, SMTP/recipient delivery, live model evals, deployment. No commits, pushes or publication.
Spec changes: none; no API gap required a backend change. Full-phase verification remains with the owner.

## Files changed in this frontend pass

23 frontend files plus this report, `EVENT_WORK.md` and `docs/roadmap/phases/STATUS.md` (26 total).
Existing backend and generated `frontend/src/api/schema.d.ts` changes were already present and left untouched.

New:

```text
frontend/src/features/ai/ActionCard.tsx
frontend/src/features/ai/DeliveryStatus.tsx
frontend/src/features/ai/SendDelaySetting.tsx
frontend/src/features/ai/WorkQueuePage.tsx
frontend/src/features/ai/actions-api.ts
frontend/src/features/ai/actions.test.tsx
frontend/src/features/ai/useClock.ts
```

Modified:

```text
frontend/src/app/router.tsx
frontend/src/features/ai/ActivityPage.tsx
frontend/src/features/ai/AssistantPanel.tsx
frontend/src/features/ai/AutonomyPage.tsx
frontend/src/features/ai/README.md
frontend/src/features/ai/api.ts
frontend/src/features/ai/autonomy.test.tsx
frontend/src/features/ai/routes.tsx
frontend/src/features/approvals/approvals.test.tsx
frontend/src/features/approvals/hooks.ts
frontend/src/features/approvals/pages/ProposalDetailPage.tsx
frontend/src/features/purchasing/SupplierDialog.tsx
frontend/src/features/purchasing/purchasing.test.tsx
frontend/src/features/purchasing/recovery.test.tsx
frontend/src/lib/permissions.ts
frontend/src/locales/en.json
EVENT_WORK.md
docs/roadmap/phases/STATUS.md
```
