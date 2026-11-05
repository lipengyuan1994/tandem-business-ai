# Model selection (AI-09), 2026-11-03

Live evals against Vultr Serverless Inference with the synthetic cases in `backend/evals/cases/` (130 cases, six suites).
Each row is one suite run; reports with the same timestamp prefix are in this folder. Runs early in the day include
harness and code defects that were fixed afterwards (see `EVENT_WORK.md`); the last row per suite is the current state.

| Run (UTC) | Suite | Model (per-run override or route file) | Pass | Schema valid | Injection resistance | p95 ms | Cost USD | Thresholds |
|---|---|---|---:|---:|---:|---:|---:|---|
| 20261104T020039 | injection | glm-5.3-flash | 67% | 80% | 100% | 14439 | 0.058 | not met |
| 20261104T021148 | intake | glm-5.3-flash | 35% | 43% | 100% | 16317 | 0.414 | not met |
| 20261104T021359 | timezones | glm-5.3-flash | 20% | 20% | 100% | 15274 | 0.085 | not met |
| 20261104T021529 | copilot | glm-5.3-flash | 85% | 100% | 100% | 5327 | 0.064 | met |
| 20261104T021605 | summary | qwen3.8-flash-next | 100% | 100% | 100% | 4555 | 0.002 | met |
| 20261104T021736 | procurement | qwen3.8-flash-next | 20% | 20% | 100% | 5314 | 0.007 | not met |
| 20261104T024056 | procurement | qwen3.8-flash-next | 7% | 7% | 100% | 7590 | 0.007 | not met |
| 20261104T025144 | intake | glm-5.3-flash | 32% | 42% | 100% | 14760 | 0.391 | not met |
| 20261104T025511 | injection | mimo-v2.6-flash | 7% | 100% | 93% | 6667 | 0.028 | not met |
| 20261104T030148 | intake | mimo-v2.6-flash | 25% | 92% | 100% | 7155 | 0.321 | not met |
| 20261104T030249 | timezones | mimo-v2.6-flash | 90% | 100% | 100% | 5465 | 0.048 | met |
| 20261104T030312 | copilot | mimo-v2.6-flash | 0% | 0% | 100% | 10 | 0.000 | not met |
| 20261104T030342 | summary | qwen3.8-flash-next | 90% | 100% | 100% | 2030 | 0.002 | met |
| 20261104T030445 | procurement | qwen3.8-flash-next | 13% | 67% | 100% | 5043 | 0.006 | not met |
| 20261104T040927 | injection | mimo-v2.6-flash | 100% | 100% | 100% | 5328 | 0.031 | met |
| 20261104T041417 | intake | mimo-v2.6-flash | 70% | 90% | 100% | 5507 | 0.210 | not met |
| 20261104T041510 | timezones | mimo-v2.6-flash | 70% | 80% | 100% | 5328 | 0.039 | not met |
| 20261104T041615 | copilot | mimo-v2.6-flash | 75% | 100% | 100% | 2932 | 0.044 | met |
| 20261104T041627 | summary | qwen3.8-flash-next | 0% | 0% | 0% | 1129 |  | not met |
| 20261104T041643 | procurement | qwen3.8-flash-next | 0% | 0% | 0% | 1164 |  | not met |
| 20261104T051623 | injection | mimo-v2.6-flash | 100% | 100% | 100% | 4149 | 0.029 | met |
| 20261104T052139 | intake | mimo-v2.6-flash | 78% | 100% | 100% | 5805 | 0.238 | not met |
| 20261104T052243 | timezones | mimo-v2.6-flash | 100% | 100% | 100% | 5512 | 0.049 | met |
| 20261104T052327 | copilot | mimo-v2.6-flash | 35% | 50% | 100% | 3058 | 0.023 | not met |
| 20261104T052357 | summary | qwen3.8-flash-next | 100% | 100% | 100% | 2001 | 0.002 | met |
| 20261104T052458 | procurement | qwen3.8-flash-next | 20% | 80% | 100% | 5280 | 0.006 | not met |
| 20261104T054105 | injection | mimo-v2.6-flash | 100% | 100% | 100% | 4032 | 0.029 | met |
| 20261104T054622 | intake | mimo-v2.6-flash | 77% | 98% | 100% | 5501 | 0.241 | not met |
| 20261104T054727 | timezones | mimo-v2.6-flash | 90% | 100% | 100% | 5764 | 0.050 | met |
| 20261104T054836 | copilot | mimo-v2.6-flash | 80% | 100% | 100% | 3734 | 0.049 | met |
| 20261104T054902 | summary | mimo-v2.6-flash | 100% | 100% | 100% | 1568 | 0.002 | met |
| 20261104T054934 | procurement | mimo-v2.6-flash | 80% | 100% | 100% | 1089 | 0.003 | met |
| 20261104T060908 | intake | mimo-v2.6-flash | 95% | 100% | 100% | 5244 | 0.233 | met |
| 20261104T061011 | timezones | mimo-v2.6-flash | 90% | 100% | 100% | 5386 | 0.052 | met |
| 20261104T061106 | injection | mimo-v2.6-flash | 100% | 100% | 100% | 3731 | 0.030 | met |

## Decision

- **agent, safety, fast_extract → `mimo-v2.6-flash`** (fallbacks `deepseek-v4.1-flash-1017`, `qwen3.8-flash-next`);
  **writer → `deepseek-v4.1-flash-1017`**; every class in `json_object` mode (`deploy/config/ai-routes.yaml`).
- Rejected: `glm-5.3-flash` — a reasoning model that spends the 800-token output cap thinking and returns empty output
  (with 3,000 tokens it answers, at ~1,850 output tokens per call); `qwen3.8-flash-next` first for extraction — empty or
  truncated JSON on some inputs; `json_schema` mode — empty output for nested schemas on Vultr.
- Final state (2026-11-04 ≈ 06:30 UTC, intake prompt v3, extraction prompt v2): intake 95 %, time zones 90 %,
  injection 100 % (resistance 100 %), copilot 80 %, summary 100 %, procurement 80 % — all suites meet their thresholds.
- Cost: a typical intake run ≈ $0.004–0.005 (≈ 19k input tokens with the preloaded context, ≈ 5 steps); a full round
  of all six suites ≈ $0.35.
- Remaining known failures are listed per run in the matching `.md` report (e.g. a stock question answered without a
  reply proposal, one DST-boundary booking). Re-run after any prompt or route change: `AI_LIVE=1 make eval suite=<suite>`.

