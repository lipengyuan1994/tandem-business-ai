# AI build handoff (current state)

**Rules for this file.** It is the *only* memory between sessions. After each card, **rewrite** it (do not append a
diary): keep it under 80 lines and true *now*. Older detail belongs in the card reports in `EVENT_WORK.md`. Write facts
a fresh agent needs to start the next card. The module card `backend/app/modules/ai/README.md` lists every file,
function and endpoint.

## State (2026-11-03 ~21:30 UTC)

- **Built (all offline, against the scripted fake provider):** AI-01 skeleton, AI-02 gateway, AI-03 router and
  policies, AI-05 runner, AI-06/07 26 tools, AI-08/08B intake, AI-10 copilot (SSE), AI-11 summary, reorder note and
  briefing, AI-12 procurement, AI-13 screens (wired to generated types), AI-09 eval harness with 130 cases, AI-14 safety
  pass offline part (`docs/security/2026-11-03-ai-review.md`).
- **Not done:** AI-04 live probe, AI-09 live runs and model selection, AI-14 live injection run (all need the key);
  AI-15 (optional: campaign draft, payment follow-up — the latter blocked by the permission question below).
- Dev stack: `.env` uses `AI_ROUTES_FILE=/config/ai-routes.dev.yaml` and `AI_FAKE_SCRIPT_FILE=/config/ai-fake-script.dev.yaml`
  (every class → `fake`, one scripted reply per use case; procurement uses `procurement_fake.py` bindings).
- **Going live:** create `deploy/config/ai-routes.yaml` from `ai-routes.example.yaml` with the Vultr models the probe
  lists, set `VULTR_INFERENCE_API_KEY` in the server `.env` (never in git), run AI-04, then
  `AI_LIVE=1 make eval suite=<intake|injection|timezones|copilot|summary|procurement> model_class=… provider=vultr model=…`.
- Contract changes so far: `effect_key` includes `trigger_key` (§4); copilot thread models are `CopilotThreadOut` /
  `CopilotThreadDetailOut` (§14, name clash with the Inbox); status reasons `ai.not_configured` (no usable key) and
  `ai.budget_exceeded`; repairs count as steps and against `max_steps`.
- Additions outside `ai`: `approvals.find_open_proposals(item_id=)`, `purchasing.find_open_quote_request(item_id)`.
- Tests (2026-11-03 00:59 UTC): 1,165 backend, 271 frontend; `make e2e` 30 (dev, incl. the AI panel); `make rehearse` passed (29 e2e).

## Known issues / open questions

- **No Vultr Serverless Inference key yet** (owner). Model quality, latency, cost and live injection resistance are
  unmeasured.
- **Q (owner): `propose_payment_reminder` is denied** for assistant contexts (the executor checks `sales.document.write`
  at creation; not in `AI_SYSTEM_PERMISSIONS`). Blocks U10.
- **Q (owner): `pyyaml`** is imported by the route loader but arrives only through `uvicorn[standard]`.
- Accepted for the event (AI review S7): the model's own tool-call JSON and repair history can repeat text it saw inside
  an untrusted fence; every write is still a proposal a person approves. S8: budget can overshoot by in-flight calls.
- Copilot may use `hold_slot` when the user may write appointments (§10 lists it for U1 only).
- The test database lives on a 3.9 GB tmpfs; do not run two full backend suites at once (WAL fills it).
- Dev worker reloads on Python changes; non-Python changes need
  `docker compose -p smb -f deploy/compose/compose.dev.yml --env-file .env restart worker`.

## Next up

1. Owner: Vultr Serverless Inference key → AI-04 probe → AI-09 selection → AI-14 live injection run.
2. P4-09 first Vultr deployment, then the demo script rehearsal (P6-04) and the judge README (P7-02).
3. AI-15 if time allows.

## Environment notes

- Test database default: `localhost:5433/smb` (container `db-test`); extra ones via `make test-db-new name=<n>`.
- Backend: `cd backend && uv run pytest -q` (Codex sandbox: `.venv/bin/python -m pytest -q -p no:cacheprovider`).
- Frontend: `cd frontend && pnpm exec tsc -b && pnpm lint && pnpm exec vitest run`.
- Codex cannot use Docker: `make check`, `make gen-api`, `make e2e`, `make rehearse` are run by the supervisor.
