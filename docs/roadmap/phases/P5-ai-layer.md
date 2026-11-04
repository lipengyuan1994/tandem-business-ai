# P5 · AI layer (Nov 3–6) — EVENT

> **Do not start any task in this file before 2026-11-03 15:00 UTC.** Record the start time of each task. All work here is the event's core AI functionality.

**Goal:** the assistant does intake and copilot work on Vultr Serverless Inference (or a cheaper or better model chosen by evals), creating the same proposals staff created by hand in P3.
**Entry:** kickoff; P3 exit gate; Serverless Inference key available (P4-09 step 2).

> **Build from the AI build kit, not from this page.** Each task below is split into self-contained cards in
> [`../ai/`](../ai/README.md) with exact interfaces ([contracts](../ai/contracts.md)) and context packs. Mapping:
> P5-01 → AI-02, AI-03, AI-04 · P5-03 → AI-09 · P5-04 → AI-01, AI-05 · P5-05 → AI-06, AI-07 · P5-06 → AI-08, AI-08B ·
> P5-07 → AI-10 · P5-08 → AI-11 · P5-09 → AI-13 · P5-10 → AI-14 · P6-02 (AI part) → AI-15 · new U9 procurement → AI-12.
> PRE groundwork (allowed before kickoff): PR-00, PR-01, PR-02, PQ-01, PQ-02. The text below stays as the original intent.

### P5-01 · Inference gateway, router and catalog probe
Kind: EVENT · Tier: A · Module: M13 · Size: L · Depends on: P4-09 (key), P3 gate
Read first: [08 Providers and model classes](../08-ai-layer.md#providers-and-model-classes), [M13](../modules/M13-ai.md), [ADR-007](../03-decisions.md#adr-007-ai-provider-agnostic-gateway-vultr-serverless-inference-by-default-json-schema-agent-protocol)
Create: `app/modules/ai/gateway/*`, `deploy/config/ai-routes.example.yaml`, `python -m app.modules.ai.probe`.
Steps:
1. OpenAI-compatible adapter (chat with `response_format` json_schema when supported, falling back to `json_object` plus schema in the prompt; embeddings; audio transcription; speech) and Anthropic adapter (messages; JSON via a prefilled response instruction). Both go through `platform.http.client()` with the egress allowlist.
2. Router: policy filter (including the `regulated` policy and the per-provider `baa` flag from ADR-018, failing closed), ordered fallback on timeout, 5xx or 429, and per-call timeouts. Test: with `DATA_PROFILE=regulated` and no `baa: true` provider, every AI feature reports disabled and no request leaves the VM.
3. Probe: lists `/v1/models`, sends a fixed JSON-schema test to each candidate in [08](../08-ai-layer.md), and writes `evals/reports/<date>-probe.md` (schema support, latency, listed price).
Done when:
- [ ] Gateway unit tests with recorded fixtures pass (no live calls in `make check`).
- [ ] The probe report exists for at least four Vultr text models.

### P5-02 · Local CPU model benchmark (strict-private feasibility)
Kind: EVENT + DEPLOY · Tier: A · Owner approval for the temporary VM · Size: M · Depends on: P5-03 (eval harness)
Read first: [08 CPU-only feasibility](../08-ai-layer.md#cpu-only-local-model-feasibility)
Done when:
- [ ] Report `evals/reports/<date>-local-cpu.md` with p50/p95, schema-valid rate and accuracy per class; VM destroyed and deletion recorded; the decision for strict-private mode is written into [08](../08-ai-layer.md) and the route file.

### P5-03 · Eval harness, cases and model selection
Kind: EVENT · Tier: A (harness) / B (cases) · Size: L · Depends on: P5-01
Read first: [08 Evaluation](../08-ai-layer.md#evaluation)
Create: `evals/runner.py`, `evals/cases/{intake,copilot,injection,timezones}/*.yaml` (60 / 20 / 15 / 10 cases), `make eval`.
Steps: cases use the seed dataset (fixture IDs). The runner seeds a scratch DB, runs the use case and scores it. Run all candidates for `agent` and `fast_extract` after P5-06 lands, pick the winners and update `ai-routes.yaml`.
Done when:
- [ ] Selection report committed; chosen routes meet the thresholds, or the gap is documented with a mitigation.

### P5-04 · Agent protocol, runner, recording and budgets
Kind: EVENT · Tier: A · Size: L · Depends on: P5-01
Read first: [08 Agent protocol](../08-ai-layer.md#agent-protocol), [M13](../modules/M13-ai.md)
Create: `ai/agent/{protocol,runner,context}.py`, `ai/usage.py`, migrations for `ai_runs`, `ai_run_steps`, `ai_usage_daily`, `assistant_threads`, `assistant_messages`; prompt file loader with front matter.
Done when:
- [ ] Runner tests from M13 pass (invalid JSON repair, step limit, budget, tool permission denial, fencing).

### P5-05 · Agent tools
Kind: EVENT · Tier: A · Size: M · Depends on: P5-04
Read first: [08 Tools](../08-ai-layer.md#tools)
Create: `ai/agent/tools/*.py`, each calling only `public.py` functions.
Done when:
- [ ] `test_tools_have_no_business_id_argument` and `test_read_tools_cannot_mutate` pass; each tool has a unit test with seeded data.

### P5-06 · Intake use case
Kind: EVENT · Tier: A · Size: L · Depends on: P5-05
Read first: [08 Use cases](../08-ai-layer.md#use-cases-mvp-unless-marked), [01 Flagship workflow](../01-product-and-scope.md#the-flagship-workflow-demo-script-backbone)
Create: `ai/usecases/intake.py`, `ai/prompts/intake.v1.md`, subscriber for `conversations.message_received` → job `ai.intake`, internal staff note with a summary and links.
Done when:
- [ ] Intake eval ≥ thresholds on the chosen route; `test_intake_idempotent_per_message`, `test_ambiguous_customer_requires_human`, `test_intake_ignores_staff_and_assistant_messages` pass.
- [ ] Manual: the public chat message from the demo script produces a booking proposal with a hold and a drafted reply within 20 s.

### P5-07 · Copilot chat (SSE)
Kind: EVENT · Tier: A · Size: M · Depends on: P5-05
Create: `ai/usecases/copilot.py`, `ai/prompts/copilot.v1.md`, SSE endpoint, threads API. Concurrency limit: a semaphore of 4 runs per API process.
Done when:
- [ ] Copilot eval ≥ thresholds; an SSE stream test covers step, proposal, final and error events.

### P5-08 · Summary, suggested reply, reorder note, briefing
Kind: EVENT · Tier: B (Tier A review) · Size: M · Depends on: P5-04
Done when:
- [ ] Each has a prompt file, a unit test with a recorded model response and at least five eval cases. The reorder note never changes the deterministic quantity (test).

### P5-09 · Frontend AI features
Kind: EVENT · Tier: B · Size: L · Depends on: P5-06, P5-07, P5-08
Read first: [M13 UI](../modules/M13-ai.md#ui), [09](../09-frontend.md)
Create: assistant slide-over (streaming steps, proposal chips), Inbox Summarise and Suggest reply, AI badge and evidence in approvals, AI settings page (policy text, budget, usage, runs), Today briefing card, AI-disabled banners.
Done when:
- [ ] Component tests (SSE mocked) pass; axe clean; manual demo run.

### P5-10 · Safety pass
Kind: EVENT · Tier: A · Size: M · Depends on: P5-06
Steps: safety-class screening on public chat input; injection eval suite; verify rate limits and budgets end to end; confirm no prompt text in logs.
Done when:
- [ ] Injection suite 100 %; a log grep for seeded canary strings returns nothing; flagged input leads to handoff, not intake.

## Exit gate P5
- [ ] `make eval` meets the thresholds in [08](../08-ai-layer.md#evaluation) on the chosen routes (report committed).
- [ ] Flagship workflow runs with real AI on the demo deployment.
- [ ] Cost per intake run measured and recorded.
