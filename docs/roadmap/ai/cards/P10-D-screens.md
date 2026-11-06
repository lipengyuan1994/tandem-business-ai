# P10-D screens — staff voice dictation

2026-11-06 · EVENT · Codex · frontend only.

Implementation and frontend verification complete. Repository-wide verification is blocked by concurrent
backend secret-scanner finding; browser verification is blocked by this sandbox's Chromium launch denial.
No backend/API/schema changes, dependencies, installs, external network calls, real keys, commits or deployment.
TypeSafe was considered: this deterministic UI uses the existing backend for STT/polish and adds no AI service.

## Behavior and files

- `frontend/src/features/ai/useDictation.ts`: one recording/insertion lifecycle. First supported WebM/Opus,
  Ogg/Opus, then MP4; mono and 24 kbps requested, 1-second chunks, 4 MiB local cap, 120-second auto-stop.
  Tracks stop before upload, on errors/cancel/unmount, and if consent arrives after cancellation. A hidden tab
  cancels recording; losing window focus stops held recording. Constraints are requests, not proof that
  every device/codec actually produces mono or obeys the bitrate.
- `DictationButton.tsx`: shadcn buttons, toggle/hold selector, pointer and keyboard hold, Alt+Space only in the
  focused textarea (plain Space, IME, Ctrl/Alt screen-reader modifiers untouched), Esc/Cancel. Pulse uses
  `motion-safe:animate-pulse`; elapsed m:ss, limit note, live announcements and changing pressed/labels.
  Native title tooltip and accessible description document the shortcut and edit-before-send behavior.
- `dictation-api.ts`: generated request/response aliases and `api.POST`, multipart serializer, same-origin
  credentials/CSRF middleware, one UUID Idempotency-Key per recording. Repeated entity_ids. One explicit
  network retry reuses audio/context/IDs/key; audio is then discarded, including on replay_unavailable or
  operation_in_progress. No polling or automatic/new-key retry.
- `frontend/src/components/ui/textarea.tsx`: opt-in dictation prop (true or options), preserves normal props,
  refs and controlled onChange. Native value setter + input event inserts at saved cursor/selection. If
  edited during transcription, uses the current selection instead of overwriting edits. Raw substitution
  replaces the last insertion when unchanged; otherwise uses current selection. Overflow errors without
  truncation. It never submits the containing form.
- `AssistantPanel.tsx`, `conversations/InboxPage.tsx`, `knowledge/ArticleEditor.tsx`: assistant, Inbox reply /
  internal note, knowledge body. Contexts assistant/inbox_reply/knowledge. Inbox passes conversation/customer
  UUIDs; existing articles pass their UUID. Copilot thread IDs are not dictionary entities and are omitted.
- Other opt-ins: approvals reject comment/reply/stock note; CRM note; inventory adjustment note; campaign
  creation/editor body; sales notes/reminder body; purchase-order and supplier notes, quote request/follow-up
  message, receiving reason. Each passes context. Structured JSON, postal addresses and pasted original
  supplier evidence remain ordinary textareas. No visitor widget code or microphone was added.
- `AiSettingsPage.tsx`: Voice dictation: off — backend reason for unavailable model_classes.stt. Missing STT
  telemetry says status unknown. Control uses STT availability independently of text-model availability,
  hides without backend permission ai.assistant.use/browser support/available status, and disables with the
  backend reason after upload-time voice.unavailable. Disabled/read-only textarea props hide the control.
- `frontend/src/locales/en.json`: all dictation copy, including permission/no-device/size/duration/invalid
  recording/rate-limit/network/policy/permission-change/replay/in-progress/server errors.
- Tests: `frontend/src/features/ai/dictation.test.tsx`, `settings.test.tsx`, `frontend/e2e/dictation.e2e.ts`
  and `frontend/e2e/dictation.config.ts`. Usage: `frontend/src/features/ai/README.md`. Own sections/row only
  in EVENT_WORK, HANDOFF and STATUS.

Audio is never written to storage, IndexedDB, files, browser speech services or URLs. No object URL is created,
so none needs revocation. References are released on completion/cancel/terminal failure/retry completion/new
recording/unmount. Last raw/polished text exists only in component memory for review. Cancel during upload
aborts the request and prevents insertion; it cannot undo work already received by the server. Before upload,
Esc/Cancel discards without a transcription request.

## Requirement-to-evidence map

Dictation unit suite: 42 tests, mocked MediaRecorder/getUserMedia + MSW. Multipart parsing uses Node streaming
Blob/File/FormData to match Node Request rather than incompatible jsdom binary wrappers; actual serialized
multipart assertions are retained.

| Requirement | Evidence / conditions |
|---|---|
| Toggle, pointer hold/release, Space/Enter keyboard hold | Mock recorder tests; no form submit |
| Mode selector and lost pointer | UI selection + pointer-cancel test |
| Alt+Space focused field, repeats, plain Space, assistive modifiers | Keyboard test and tooltip assertion; OS/assistive technology untested |
| Pulse, elapsed, auto-stop at 120 s and note | Recording/m:ss tests with fake timers; reduced-motion class in source, visual rendering untested |
| Esc / Cancel no upload; tracks stopped | Separate cancel tests; late consent, recorder error, hidden-tab and unmount tests |
| Multipart context, IDs, audio, CSRF, Idempotency-Key | MSW request.formData() + headers assertions |
| Recording / Transcribing / Inserted announcements | Live-region assertions; real screen-reader announcements untested |
| Cursor insertion, selection replacement, never send | Controlled form state/caret/submit spy assertions; no send endpoint used |
| Edits during transcription / field limit | Deferred MSW response; edit preserved; overflow rejected without truncation |
| polished=false note, show raw, use raw | Note and raw toggle/substitution tests |
| Permission denial / missing device / device failure and focus | DOMException tests; editable text fallback |
| Permission/browser/codec/STT unavailable hiding | PermissionGate and missing API/codec/status tests |
| Backend unavailable reason and disabled tooltip | MSW voice.unavailable test |
| Long/large/invalid audio, 429, 403, generic errors | Parameterized MSW tests; local >4 MiB rejection |
| Same-key network retry once, then discard | Two-failure and success-retry tests; replay-unavailable-on-retry test |
| replay_unavailable / operation_in_progress | Exact code tests; no retry/poll offered |
| Upload cancellation | Deferred response + Escape; field unchanged |
| No dictation storage/object URLs | Storage writes/enumeration, IndexedDB.open and URL.createObjectURL spies untouched; only MSW logger reads debugLevel, no application storage reads |
| Ordered MIME fallback | WebM and Ogg/MP4 fallback tests; real Safari decoding untested |
| Settings off/reason and absent telemetry unknown | Two new settings.test.tsx tests |
| Browser permission-denied focus and fake-media recording/Escape | Offline Playwright specs added; blocked before browser launch, unverified |

## Verification

Native Node reports arm64; installed Rolldown binding is Mach-O arm64. Existing uv/python3/backend interpreter
were verified arm64 before Make invocation. Installed Chromium headless shell is also arm64.

- `cd frontend && pnpm typecheck` (tsc -b): PASS.
- `cd frontend && pnpm lint`: PASS.
- `cd frontend && pnpm exec vitest run`: 40 files, 424 tests passed, 21.69 s. Prior 380 tests plus 42 dictation
  and 2 status tests. Existing non-failing jsdom scrollTo diagnostics remain. Development failures from
  multipart runtime mismatch, DOMException classification and new test expectations were corrected; no
  existing test was disabled or weakened.
- `cd frontend && pnpm build`: PASS offline; Vite 545 ms after tsc. Vite main 59.14 kB gzip (baseline 59.12).
  Independent Node gzip: main 58,419 → 58,436 bytes (+17); all emitted JS/CSS 397,875 → 402,853
  (+4,978 bytes, ~4.86 KiB). Widget entry unchanged at 4,981 bytes gzip. Asset hashes can change with imported
  asset names. Aggregate JS/CSS growth is also below the requested ~10 KB.
- `git diff --check`: PASS at frontend verification time.
- `pnpm exec playwright test --config=e2e/dictation.config.ts`: 2 launch failures; no browser assertions ran.
  Chromium aborts at bootstrap_check_in / MachPortRendezvousServer: Permission denied (1100). No downloads or
  sandbox bypass attempted. Both tests intercept built assets and synthetic APIs, abort other origins, and
  use fake-ui/fake-device flags. They bypass normal e2e global setup (which resets the dev DB), require a
  preceding build, and start no HTTP server. Re-run outside the restricted sandbox with that command.
- Required repository check attempted with offline restrictions:

  ```sh
  UV_CACHE_DIR=/private/tmp/p10-d-uv-cache UV_NO_SYNC=true UV_OFFLINE=true \
  UV_PYTHON_DOWNLOADS=never RUFF_CACHE_DIR=/private/tmp/p10-d-ruff-cache \
  MYPY_CACHE_DIR=/private/tmp/p10-d-mypy-cache PYTHONDONTWRITEBYTECODE=1 \
  DEPLOY_CHECK_NO_DOWNLOADS=1 DEPLOY_CHECK_OFFLINE=1 \
  make -k check -o test-db -o backend-test
  ```

  Final run: fixtures PASS (149 rows / 30 tables); Ruff/format PASS (635 files), mypy PASS (339 sources),
  frontend lint/types and 424 tests PASS; offline deployment regressions PASS (106 tests, 19 skips, 81.076 s),
  shellcheck/installer dry run PASS; backend/frontend licenses PASS. FAIL at secrets: Gitleaks flags
  backend/app/modules/ai/chains.py:25 (generic-api-key), a concurrently edited backend file left untouched.
  Only redacted report metadata was inspected; the finding is not a confirmed exposed credential. An
  earlier run found 29 backend lint issues, which cleared during concurrent work. This is not an
  unrestricted make check pass: DB startup/tests were excluded; offline deploy skips Compose resolution,
  Caddy runtime, Terraform provider and cloud-init schema. Backend/hosted behavior remains unverified.

No backend API gap blocked implementation. Mocked frontend tests do not verify live speech-to-text, actual
microphone/codec behavior, Safari, browser layout or assistive technology. No deployment occurred.

## Supervisor check (2026-11-06)

`pnpm build` and `pnpm exec playwright test -c e2e/dictation.config.ts` on the developer Mac: the permission-denied
test passes; the fake-microphone recording test cannot run there because headless Chromium 156 never resolves
`getUserMedia` on macOS (reproduced with a minimal page, with and without granted permissions; likely macOS
microphone privacy around the fake device). Added `permissions: ["microphone"]` to the config (needed in headless
mode). Run the recording test on Linux CI or check by hand in a browser before relying on it.
