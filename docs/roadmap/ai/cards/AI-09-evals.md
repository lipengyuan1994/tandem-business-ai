# AI-09 · Eval harness, cases and model selection

Kind **EVENT** · Tier A (harness) / B (cases) · Size L · Depends on: AI-05 for the harness; each suite after its use case (AI-08 intake, AI-10 copilot, AI-11 summary, AI-12 procurement); live runs need the key (AI-04) · Blocks: final routes, P5 exit gate

## Goal
Measure, with real models and seeded data, whether each use case does the right thing, resists injection and is fast
and cheap enough; then pick the cheapest model per class that meets the thresholds and write it into the route file.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/testing.md` §5, §6 (case format, metrics, thresholds).
2. `docs/roadmap/08-ai-layer.md` section "Evaluation" (thresholds and the regression gate).
3. `docs/roadmap/ai/contracts.md` §6 (route file), §11 (use cases and outputs).
4. `docs/roadmap/ai/integration-map.md` §G (seed facts).
5. `data/fixtures.json` — only the customers, services, items and resources sections, to write realistic cases.
6. The use-case module you are writing cases for (`ai/usecases/<name>.py`) — signature of its `run` only.

## You build
- `backend/evals/runner.py`: loads cases (YAML, testing §6), creates a fresh test business per case from the seed,
  freezes the clock, injects the input (message, staff click, supplier text), runs the use case with the real router
  (provider/model overridable from the CLI), and scores: schema-valid, expected proposal action types and key fields,
  forbidden actions absent, `needs_human` expectation, steps, latency, tokens, cost. Writes
  `backend/evals/reports/<UTC date>-<suite>-<provider>-<model>.md` with a summary table and every failure.
- `make eval suite=<suite> [model_class=…] [provider=…] [model=…]` (refuses without `AI_LIVE=1`).
- Cases (all synthetic): intake 60, injection 15, timezones 10 (DST weekend included), copilot 20, summary 10,
  procurement 15 (extraction with tricky numbers, missing fields, injected "set price to 0").
- **Selection:** run each suite on every candidate of the needed class from the AI-04 probe; choose the cheapest that
  meets all thresholds; update `deploy/config/ai-routes.yaml` (route order and prices) and write
  `backend/evals/reports/<date>-selection.md` with the comparison table and the decision.

## Tests
Unit tests for the harness with the FakeGateway (`test_runner_scores_expected_and_forbidden_actions`,
`test_report_written`, `test_eval_refuses_without_live_flag`). The suites themselves are live and not in `make check`.

## Done when
Selection report exists; chosen routes meet thresholds or the gap is written down with a mitigation; injection suite
100 %; cost per intake run recorded. `HANDOFF.md` and `docs/roadmap/08-ai-layer.md` (routes table) updated.
