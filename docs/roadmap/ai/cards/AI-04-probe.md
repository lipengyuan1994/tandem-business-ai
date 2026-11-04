# AI-04 · Live catalog probe and first route choice

Kind **EVENT** · Tier B · Size S · Depends on: AI-03, a Vultr Serverless Inference key in the environment (owner) · Blocks: AI-09 model selection

## Goal
Find out, with real calls, which Vultr models answer our JSON protocol reliably and fast, at what price, and write a
first `ai-routes.yaml` for the demo. This is the only card (with AI-09) that calls live models.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/contracts.md` §5, §6.
2. `docs/roadmap/ai/testing.md` §5.
3. `docs/roadmap/08-ai-layer.md` "Providers and model classes" (candidates and prices as listed on 2026-11-03).
4. `backend/app/modules/ai/gateway/` (your AI-02/AI-03 code).

## You build
- `backend/app/modules/ai/probe.py`, run as `python -m app.modules.ai.probe [--provider vultr]`:
  1. `GET {base_url}/models` and print the model list (id, and price fields if present).
  2. For each candidate text model: send a fixed request asking for `{"kind":"final","message_to_staff":"ok"}` with this
     probe's own small JSON schema (two required string fields `kind`, `message_to_staff`), once with `json_mode=
     "json_schema"` and once with `"json_object"`; record schema-valid yes/no, latency, tokens.
  3. Write `backend/evals/reports/<UTC date>-probe.md`: table per model and mode, plus the date.
- Read the key from the environment only; never print it, never write it to a file.
- Propose `deploy/config/ai-routes.yaml` (not committed if it contains anything secret; it should not) with the best
  candidates per class, `json_schema: true` only where the probe proved it, and prices from the catalog.

## Tests
`test_probe_report_format` with the FakeGateway (no network). The live run is manual: `AI_LIVE=1 python -m app.modules.ai.probe`.

## Done when
The probe report exists for at least four Vultr text models; the route file validates; `make check` passes;
`HANDOFF.md` lists the chosen placeholder routes and the probe date.

## Stop if
No key is available: mark the card BLOCKED with the reason; other cards continue with the FakeGateway.
