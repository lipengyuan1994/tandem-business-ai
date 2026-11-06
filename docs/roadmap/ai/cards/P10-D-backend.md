# P10-D backend — voice dictation

Status: DONE (backend implementation and offline verification). EVENT work, 2026-11-06.
The contract section and M14 were updated before implementation. D7 is 120 seconds / 4 MiB.
No frontend edits, installs, downloads, real provider calls, Docker, commits or deployment.

## Behavior and choices

POST /api/v1/voice/transcriptions requires the existing copilot permission, ai.assistant.use.
Streaming multipart stays in memory, with a hard audio/body cap and MIME/container signature checks.
STT uses Vultr Whisper or policy-allowed local compatible servers; policy, egress, deadlines and fallback
apply per attempt, including after budget locks and results. No hosted/browser escape from local-only policy.
Provider measurement >125 seconds is rejected. No message, proposal or outbound delivery is ever created.

Writer/JSON object mode performs constrained editing with dictation_polish v1. Writer was chosen because
this step edits prose, punctuation and lists. The transcript and dictionary are fenced as untrusted.
Names come from bounded public readers under explicit permissions and business RLS: catalog/SKUs,
suppliers, active staff, exact customer IDs and exact article IDs. Customers are never enumerated.
The guard checks normalized numbers/currencies, all novel words (stricter than a guessed proper-noun detector),
empty output and <=20% character growth. Rejections and optional-polish errors return cleaned raw speech.

Each STT attempt reserves 120 seconds in existing financial counters, then settles reported duration.
Unknown usage/errors retain the reservation. Text polish uses existing token/cost accounting.
Audio pricing defaults conservatively to $0.006/min when omitted; deployed Vultr explicitly uses that
unconfirmed placeholder. Operators must confirm actual billing. Explicit zero is available for free local STT.

voice_sessions and ai_runs/steps contain metadata only. No audio, raw/polished transcript or dictionary
is persisted. Metadata export, erasure and 90-day bounded cleanup use the existing lifecycle mechanisms;
voice registers a cleanup handler invoked by the existing ai.assistant_retention job.

30 new operations/user/hour. Durable idempotency claims bind business/user/key and input/permissions digest.
Completed text is cached only in process memory (10 minutes, maximum 128 responses, explicit expiry).
Another process, restart or expiry returns 409 voice.replay_unavailable. Active/failed claims never repeat
inference; a new key represents a new operation. This is a documented privacy tradeoff, not durable text replay.

## Evidence map

| Requirement | Evidence |
|---|---|
| Multipart STT, JSON, language, safe filename, local duration compatibility, provider errors | ai/tests/test_transcription_gateway.py |
| 120-second reservation, measured settlement, retained unknown usage, no-call financial denial | same gateway tests, real isolated PostgreSQL |
| Local-only/regulated, effective-policy recheck after reservation, allowlist/egress, fallback deadline | gateway tests and integration voice disabled/local tests; existing P10-E0 suite |
| Audio memory-only on success/error/interruption; cap before provider; exact cap; MIME/magic; >125 s | tests/integration/test_voice_dictation.py |
| Assistant permission, 30/hour, replay/mismatch/concurrent keys, no message creation | same integration tests |
| Scope by permissions/business, metadata RLS/export/erasure/retention, no transcript in recordings | same integration tests |
| New number/name/currency/length, empty output, optional failure/budget fallback | ai/tests/test_dictation_guard.py and voice integration tests |
| >=20 synthetic cases, >=85% threshold, fallback metrics, no external calls | 24 cases; ai/tests/test_dictation_evals.py with scripted responses and production polish/runtime |
| Same-origin microphone, camera/geolocation disabled | deploy/tests/test_voice_permissions.py; native caddy validate |
| OpenAPI | backend command: .venv/bin/python -m app.cli openapi > openapi.json |

Offline scripted expected-output checks are infrastructure/guard checks, not evidence of live model quality.
The supervisor owns live dictation evals. Browser recording, microphone consent, UI confirm/edit flow,
real codec decoding and deployed behavior remain untested in this backend-only task.
Local OpenAI-compatible STT is requested in verbose_json for standard duration metadata; missing duration fails closed.
Conservative guards may retain raw text for unsupported number/language formats; they do not prove semantic fidelity.

## Files changed

- AI: gateway/{types,routes,router,openai_compat,pricing,fake}.py; model_calls.py, reservations.py,
  models.py, schemas.py, public.py, jobs.py; dictation.py, dictation_dictionary.py, dictation_guard.py;
  prompts/dictation_polish.v1.md; README.md; dictation/transcription tests and the dev-script key assertion.
- Voice: __init__.py, registration.py, api.py, upload.py, service.py, models.py, lifecycle.py, README.md.
- Public names-only readers and module cards: identity, purchasing, knowledge.
- Migration: backend/migrations/versions/20261106_0800_voice_dictation.py.
- Integration: backend/tests/integration/test_voice_dictation.py.
- Existing export assertion: backend/app/modules/admin_ops/tests/test_data.py now requires the voice module
  and its empty seeded voice_sessions export; all previous isolation/credential checks remain.
- Evals: runner.py, runtime.py, README.md, suites/dictation.yaml, cases/dictation/*.yaml (24).
- Deploy: compose/Caddyfile, config/ai-routes{,.example,.dev,.local.example}.yaml,
  config/ai-fake-script.dev.yaml, tests/test_voice_permissions.py.
- API/roadmap: backend/openapi.json; contracts.md; M14; P10 phase/STATUS; this card; EVENT_WORK.md.

## Verification

Existing Python/uv/Node/Caddy are native arm64. All 263 installed compiled Python extensions contain arm64;
uvloop is universal with an arm64 slice. No Intel runtime was selected.
All test runs use only smb_voice on localhost:5433, with the supplied application and owner URLs.
The new migration was applied; migration round-trip is included in the full backend suite.

Initial focused tests: 40 passed, then 62 passed (9.08 s) after dictionary/accounting/eval additions.
Ruff check/format and mypy passed; backend OpenAPI regenerated.
Native Caddy: Valid configuration. Offline deployment: 106 tests, 19 skipped, ALL PASSED.
Final focused regression run: **82 passed in 14.88 s**, including the production polish/eval runtime,
dictionary cap priority, accounting, privacy/lifecycle, strict import boundaries and updated export/dev assertions.

Final required full backend command: **2,083 passed in 1141.30 s (19:01)**:
cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider
Both supplied TEST_DATABASE_URL and TEST_DATABASE_MIGRATION_URL pointed only to smb_voice.

Final restricted make check: **ALL PASSED** — fixtures 149 rows / 30 tables; Ruff check/format (622 files);
mypy (328 source files); frontend lint/types and **380 tests / 39 files**; deployment **106 tests, 19 skipped**;
backend/frontend licences; gitleaks **no leaks**. Backend tests were excluded from this Make invocation and
verified separately by the full command above. Native caddy validate: **Valid configuration**.
Backend OpenAPI was regenerated using the project's CLI; the full suite verifies it matches the app.
git diff --check passed.

First full run: **2,007 passed, 3 failed, 62 setup errors in 1135.22 s**. Two strict assertions needed
voice added to export/dev-script lists; the other failure caught cross-module test imports. Moving those tests
during that active run caused the 62 collected-package import errors. The file layout was fixed before the
82-test focused run and final full run. No failing test was disabled or weakened.

No independent second-model review, live dictation eval, real STT-server/codec test, browser test or deployment
was performed. Those checks remain explicitly unverified; scripted golden-output tests do not establish
live accuracy or latency. The code and diff are ready for owner review.

The unrestricted make target starts Docker. To respect the task's prohibition, use:
UV_CACHE_DIR=/private/tmp/p10-d-uv-cache UV_NO_SYNC=true UV_OFFLINE=true UV_PYTHON_DOWNLOADS=never
DEPLOY_CHECK_NO_DOWNLOADS=1 DEPLOY_CHECK_OFFLINE=1 make check -o test-db -o backend-test.
The backend suite runs separately on the existing isolated DB. Offline deployment excludes Docker Compose
resolution, runtime Caddy HTTP tests, Terraform provider and cloud-init schema checks. Native caddy validate
runs separately. This is not an unrestricted make-check pass.
