# P10-C1 part 3 — knowledge, hand-over and grounded reply screens

EVENT · Codex · 2026-11-05 UTC. **Implementation complete; frontend checks pass.**
The overall C1 phase remains IN PROGRESS: the latest restricted `make check` is blocked by unrelated backend lint;
unrestricted checks, browser/device/a11y testing and phase review are not verified by this slice.
No installs, network calls, provider/API-key use, Docker, commit, deployment or publishing were performed here.
Existing uncommitted backend/schema/roadmap changes and concurrent backend work were preserved.

## Result and requirement evidence

| Requested requirement | Implemented behavior | Executed evidence |
|---|---|---|
| Knowledge navigation/list/search/status/visibility | Lazy `/knowledge` routes; backend search and filters; draft/published/archived and public/internal badges | `knowledge.test.tsx`: list badges and query parameters; readers and denied direct routes |
| Article title/Markdown/tags/visibility, save new version | Generated typed POST/PATCH; editable creation slug; safe Markdown preview; append revision using expected article version | create/edit payload assertions, Markdown heading preview, escaped HTML and unsafe-link test |
| Explicit confirmed Publish, archive | Separate version-bound endpoints and confirmation dialogs; unsaved edits disable both; conflicts retain edits until explicit reload | publish/archive request/version assertions, no request before confirmation; conflict/reload and unsaved publish test |
| Read-only version comparison | Side-by-side immutable title/tags/body snapshots with business-local creation time; citation `?version=N` selects the historical snapshot | historical version selection and read-only editor assertion |
| Suggestions | Open questions tab; Write article prefills title; conversion occurs only on Save draft; Dismiss binds suggestion version | conversion/dismiss payload assertions and reader permission tests |
| Visitor AI disclosure | Backend profile/thread `ai_greeting`, including before chat creation | widget greeting tests |
| Talk to a person, in/after hours | Visitor-cookie hand-over POST; display the backend acknowledgement; button disabled while sending | widget tests for both backend messages and scoped POST |
| Distinct system messages | Public `is_system` flag; visible System message label, centered dashed-border style, polite live conversation list | widget flag/style/label assertions; three offline Python projection tests |
| Delayed public reply | Existing incremental merge/cursor behavior retained; empty polls retain cursor; later publication appears with previous messages intact | widget regression: empty page → delayed published reply → next cursor |
| Inbox AI mode, Take over / Hand back | Mode in list and thread; mutations gated by thread.write; hand-over request highlighted | `handoff.test.tsx`: scoped endpoints, updated mode, request reset and permissions |
| Withheld countdown and Stop | Live m:ss countdown; confirmed message Stop; approve/settings permissions; refresh relevant queues; too-late/error notice survives row removal | countdown tick, both Stop permission paths, denied Stop, Stop payload path, publication race and toast regression |
| Cancelled reply reason | Staff Inbox and Exceptions show stale/human-mode cancellation in plain words | both cancellation reasons tested on both surfaces |
| Citations and grounding on proposal/action card | Exact historical article title + version/link; overall and per-claim verdict/source; unavailable and no-claim states explicit; card revision checked against fetched proposal | `front-desk.test.tsx`: detail/card, three verdicts, article/DB/no-source, unavailable/no-claim, Knowledge access denial |
| Front-desk Autonomy rule | Existing level/shadow/cap controls; correct reply approval permission; plain fallback explanations | front-desk rule save payload, allowed/denied approver, level/shadow/limit and reasons |
| Exceptions and Work queue | API `web_reply` cancellations with reasons; withheld Work queue countdown/Stop | Exceptions and Work queue MSW tests |

All new staff copy is in `src/locales/en.json`; widget labels are in its independent `strings.ts`.
New screens use existing shadcn controls, PermissionGate and LocalTime. No TypeSafe or model call is needed for
these deterministic API-driven displays and actions, and none was made.

## Small blocking API gap

PublicMessageOut originally exposed only `from_business`, which cannot distinguish a hand-over system message from
an ordinary staff/AI reply. Added **`is_system: bool = False`**, projected from `author_kind == "system"` in
`conversations/api.py::_public_thread`. No staff identity/internal metadata, ownership logic or publication filtering
was added to the public output. Existing publication-time `created_at` projection remains unchanged.
Updated the C1 contract and conversations README. Three offline tests exercise this actual projection for system,
staff and assistant rows; the existing database hand-over test also now asserts the flag (DB test not run here).

Regenerated, without installing tools or editing generated output by hand:

```sh
cd backend && .venv/bin/python -m app.cli openapi > openapi.json
cd frontend && pnpm exec openapi-typescript ../backend/openapi.json -o src/api/schema.d.ts
```

## Verification

Existing Node, uv and Python verified native arm64. Python `platform.machine()` returned `arm64`; pydantic_core
and both psycopg_binary compiled extensions were arm64. No runtime/environment was created or extended.

Final commands in `frontend/`:

```text
pnpm typecheck                         PASS (tsc -b, including tests)
pnpm lint                              PASS (oxlint --deny-warnings src e2e)
pnpm exec vitest run                    39 files passed; 380 tests passed; 19.55 s
pnpm build                             PASS (tsc -b + Vite; built in 552 ms)
```

Vitest emits the existing jsdom `Window's scrollTo()` notices; final run has no failed tests or unhandled requests.
Initial baseline `pnpm build` stopped at the pre-existing new delivery-state mapping / Inbox fixture type errors;
both compatibility issues are fixed. Baseline sizes were therefore obtained through `pnpm exec vite build`.
During test development, two parameterization/async-act mistakes, Markdown typing syntax and retained toast state
were corrected; the final complete run includes all 33 added tests and passes without weakening assertions.

Widget sizes, Node gzipSync per transitive JS output file (all entry/preload imports; no unrelated staff chunks/maps):

| | Before | After | Growth |
|---|---:|---:|---:|
| Transitive JavaScript gzip | 77,557 bytes | 77,722 bytes | **165 bytes (0.17 KB)** |
| JavaScript + shared CSS gzip | 88,410 bytes | 88,592 bytes | 182 bytes |

Still below the existing 80 KB JS budget and well below the requested ~15 KB growth limit. Final Vite widget entry
alone: 15.80 KB raw / 4.98 KB gzip; React/shared chunks are included in the totals above.

Offline backend verification, deliberately without the default PostgreSQL pytest plugin:

```sh
cd backend
.venv/bin/python -m pytest -o addopts='' -q -p no:cacheprovider \
  app/modules/conversations/tests/test_public_projection.py \
  tests/architecture/test_openapi_current.py tests/architecture/test_route_permissions.py \
  tests/architecture/test_module_imports.py tests/architecture/test_dependency_graph.py
```

**17 passed** (3 system-message projection cases + 14 API snapshot/permissions/module-boundary/DAG checks).
Focused Ruff and format checks pass for the four conversations files touched in this slice. `git diff --check` passes.

Required make-check attempt (offline; no environment sync, database startup, database tests or deployment tooling):

```sh
UV_CACHE_DIR=/private/tmp/p10-c1-uv-cache UV_NO_SYNC=true UV_OFFLINE=true \
  make check -o test-db -o backend-test -o deploy-check
```

First attempt passed fixture validation, backend lint/format and mypy (316 files), frontend lint/typecheck, then
encountered a new test-construction mistake in the Markdown test (fixed; final full frontend run above passes).
The latest attempt stops earlier on an unrelated backend file that appeared during this session:

```text
app/modules/ai/tests/test_intake_live_regressions.py:
  7 F401 unused imports, 2 I001 import-order findings, 1 E501 long line
Found 10 errors. [9 fixable]
make: *** [lint] Error 1
```

The 10 findings were independently reconfirmed with scoped Ruff; that file was not edited here. Fixture validation
passes: 149 rows in 30 tables. The final make attempt does not reach later targets; licenses/secrets were not verified
by that invocation. This is **not** an unrestricted make-check pass, nor a successful restricted make-check pass.

## Files created/changed in this slice

```text
frontend/src/features/knowledge/{routes.tsx,hooks.ts,KnowledgePage.tsx,ArticleEditor.tsx,
  MarkdownPreview.tsx,VersionHistory.tsx,knowledge.test.tsx,README.md}
frontend/src/features/conversations/{InboxPage.tsx,hooks.ts,OwnershipControls.tsx,
  ReplyPublication.tsx,inbox.test.tsx,handoff.test.tsx}
frontend/src/features/ai/{ActionCard.tsx,ReplyGrounding.tsx,AutonomyPage.tsx,
  DeliveryStatus.tsx,WorkQueuePage.tsx,api.ts,front-desk.test.tsx,README.md}
frontend/src/features/approvals/{hooks.ts,pages/ProposalDetailPage.tsx}
frontend/src/widget/{Widget.tsx,strings.ts,widget.test.tsx,README.md}
frontend/src/{app/router.tsx,lib/permissions.ts,locales/en.json,api/schema.d.ts}
backend/app/modules/conversations/{api.py,schemas.py,README.md,
  tests/test_handoff.py,tests/test_public_projection.py}
backend/openapi.json
EVENT_WORK.md
docs/roadmap/{modules/M19-knowledge.md,phases/STATUS.md,ai/contracts.md,ai/cards/P10-C1-part3.md}
```

## Limits and follow-ups

- Real browser, mobile viewport, keyboard/screen-reader and axe/e2e testing were not run. Mobile layouts, 44px widget
  controls, labels and polite live messages are implemented; jsdom assertions are not device/a11y certification.
- Live backend/DB/worker/provider end-to-end tests and the modified DB hand-over assertion were not run under this
  no-network scope. Offline projection and API snapshot tests do not verify a deployed hand-over or publication job.
- Markdown preview intentionally supports headings/paragraphs/lists/bold/code/safe links; it is not a full CommonMark
  renderer. Raw HTML/images are displayed as text. The editor tells authors the supported subset.
- Knowledge listing is bounded to 100 results with search/filters; the backend has no pagination cursor here.
- Cards use the existing proposal-detail API for evidence because ChatCard carries no payload; mismatched revisions
  show the existing changed-action notice. Users without Knowledge permission see an explicit restricted-source label.
- Existing per-policy widget privacy wording is retained as requested by the roadmap; this slice adds the required
  backend AI disclosure. No autonomy policy, inference routing, backend permission or provider setting was changed.
- Resolve the unrelated backend lint findings and rerun the full project checks before phase verification.
