# Local-model benchmark (P5-02 / P10-E0), 2026-11-06

**Question:** can a clinic run this product's AI on a model hosted on its own hardware (ADR-020 decision 5:
`regulated` = local models only)? Measured with the project's own eval suites, unchanged, against a local
OpenAI-compatible server. No data left the machine (Vultr keys unset; route file pointed only at `localhost`).

## Setup

- Hardware: Apple M1 Pro (10-core CPU, 16-core GPU), 32 GB unified memory — a typical office workstation class.
- Server: llama.cpp `llama-server` 0.7.0 (Metal, all layers on GPU), context 32k, 2 parallel slots,
  `--jinja`, `reasoning_effort=low`.
- Model: `gpt-oss-20b` (OpenAI open-weight, MXFP4 GGUF, 11 GB; Apache-2.0). The only candidate already on disk; no
  download was made.
- Route file: every class (agent, fast_extract, writer, safety, grounding) on the local server; same limits as
  production except `step_timeout_s=90`, `run_timeout_s=240` (local prefill is slow). JSON-object mode.
- Raw throughput: generation ≈ 41–45 tokens/s; prompt processing ≈ 350 tokens/s.

## Results (per-suite reports `20261106T13*-local-gpt-oss-20b.md`)

| Suite | Cases | Pass | Schema valid | p50 | p95 | Thresholds | Hosted route (for comparison) |
|---|---|---|---|---|---|---|---|
| grounding | 10 | **100 %** | 100 % | 2.4 s | 3.5 s | met | 100 % |
| procurement (extraction) | 15 | **93 %** | 100 % | 3.2 s | 5.4 s | met | 80 % |
| summary | 10 | **70 %** | 100 % | 3.8 s | 11.0 s | met | 100 % |
| injection | 15 | 100 % | 100 % | 6.8 s | 34.7 s | not met (latency) | 100 % |
| copilot (agent) | 24 | 17 % | 96 % | 12.3 s | 41.2 s | not met | 83 % |
| intake (agent) | 67 | 43 % | 63 % | 35.9 s | 51.6 s | not met | 98.5 % |
| timezones (agent) | 10 | 0 % | 50 % | 43.1 s | 68.1 s | not met | 90–100 % |

Failure causes: agent suites mostly `ai.invalid_model_output` (25 of 67 intake cases: the multi-step tool protocol's
JSON was not valid), and wrong tool choices/arguments in copilot. No output hit the token cap. Latency is dominated by
prompt processing: an agent step carries 4–10k tokens of context, which takes 15–30 s at 350 tokens/s before the first
output token.

## Decision for `regulated` / `strict_private` installs

1. **Ship local-only routing now** (P10-E0 routing change): nothing in a regulated install calls a hosted API.
2. By the pass rule in `docs/roadmap/08-ai-layer.md` (accuracy within 5 points of the hosted winner, ≥ 98 %
   schema-valid, p95 ≤ 20 s for single-step classes), a 20B local model on workstation hardware **passes for
   `grounding` (100 %, p95 3.5 s) and `fast_extract` (supplier quotes 93 %, p95 5.4 s)**. `writer` (summaries 70 %
   vs 100 % hosted) does not pass; `safety` was not measured on its own (the injection suite, which includes it, kept
   100 % resistance, but its latency is dominated by the agent).
3. It is **not good enough for agent classes** (customer front desk/intake, staff copilot): accuracy far below the
   thresholds and 30–50 s per answer. In a regulated install these stay **off** (fail closed, the UI says why) until
   a local model passes the same suites on the clinic's hardware. Expected path: a larger instruct model (30B-class,
   non-reasoning, strong tool use) on a GPU workstation or small on-premises GPU server; re-run this benchmark there.
4. The required statement stays: PHI must be processed only by models hosted under the clinic's control; this
   benchmark shows feasibility per class, not compliance.

## Not done

- Only one model measured. Next candidates (need a download of ≈ 5–20 GB each): a 30B-class MoE instruct model and an
  8B instruct model, to find the smallest model that passes the agent suites. No rented GPU VM was used (D5).
- Local speech-to-text (whisper.cpp) and embeddings were not measured; they come with P10-D and C2.

## How to repeat

```bash
llama-server -m <model.gguf> --host 127.0.0.1 --port 8091 -c 32768 -np 2 -ngl 99 --jinja
# route file: provider {kind: openai_compat, base_url: "http://localhost:8091/v1", hosted: false, vendor: local}
LOCAL_INFERENCE_HOSTS=localhost AI_ROUTES_FILE=<that file> TEST_DATABASE_URL=<empty migrated db> \
  AI_LIVE=1 make eval suite=<suite>
```
