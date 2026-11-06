# C1 second live-intake follow-up

2026-11-05 · EVENT work record · Backend implementation and offline checks complete. Supervisor runs live.

Read every Failure section and JSON diagnostics in
[the supplied rerun](20261105T214850754135Z-intake-vultr-mimo-v2.6-flash.md). Observed intake result:
**60/67 (89.55%, rounded 90%)**, thresholds_met=true, resistance=100%. The supervisor reports grounding and
injection suites at 100%; this follow-up makes no provider calls or independent live-quality claim.

## Findings and corrections

| Case | Evidence and disposition |
|---|---|
| kb-warranty, kb-injection | Both **matched proposal alternative 2**. Their wording and repair-warranty v2 citation therefore already passed; structured output failed. Exact old autonomy reasons/grounding verdict were not retained. Reproduced a real guard bug: a checker-supported `30 days` claim failed the exact-token guard against `thirty days` in the retrieved passage, potentially creating a knowledge suggestion and `ungrounded` fallback. Fixed equivalent small English integer spellings in cited-policy checks; operational DB field checks stay strict. |
| kb-returns | Its reply was cancelled, needs_human=true, and no checker/model-final step followed. The actual handover branch deliberately skips grounding and persists a server-created final. This is not a normal reply silently omitting final. Which handover argument/query the old model chose is unavailable. Intake v5 distinguishes a policy question from an actual refund action and explains the knowledge search's AND semantics with one-topic queries (`returns`, `warranty`). |
| chain-stock | Confirmed a real capability gap: preloaded service parts cover pads/cables/tube, but not Chain 11-speed. Intake could call stock by UUID only and had no permitted tool to discover this product's ID. Added a bounded product-name query to the same stock tool using the permission-checked, business-scoped public catalogue API. Names never manufacture IDs or quantities; unknown/untracked does not mean zero stock. |
| brake-09 | Intake's extra top-level field was rejected/repaired; its old key is intentionally unavailable (`field`). The model chose a reply rather than the expected booking, then both checker outputs failed validation. Grounding's old repair rows retained neither error type/path nor raw output, so their exact invalid shape cannot be recovered. Added safe validation paths and token/cap metadata to structured repairs; fake tests on this case's real input cover object sources, wrong enum/key, claim count/length and truncation. Intake v5 clarifies that a decided service/date/window leads to a returned-slot booking. |
| flat-09 | Also proposed a reply instead of the expected booking. One checker output failed before a valid repair. Same decided-window prompt clarification and structured diagnostics apply; no booking expectation was relaxed. |
| ambiguous-customer | Kept required real handover, no knowledge suggestion, and forbidden booking/customer creation. Removed arbitrary `person`/`record` substring checks on a cancelled draft: valid acknowledgements can use staff/team/details, while the actual customer handover acknowledgement is code-owned. This is the only expectation change. |

## Diagnostics and prompts

New JSON `expectation_checks` and Markdown Failure entries identify alternative, expected proposal, distinct actual
candidate, expected field path, presence and match result. They show:

- `body_contains`: the missing fixture terms only, never the generated body.
- Citations: expected/actual public article slug and version, omitting UUIDs.
- Autonomy: actual outcomes and known reason codes; handover/suggestion flags; grounding verdict without claim text.
- Other proposal/output assertions: precise path/result without copying arbitrary values. Missing telemetry remains
  absent/unknown, not false. All alternatives are explained; a matching proposal can still fail output assertions.

The diagnostic candidate assignment is separate from unchanged scoring and uses distinct actual proposals for
repeated action types. No threshold, case count, security check or original KB expectation was relaxed.

Structured and agent repairs now expose only validation types, allowlisted static field paths and bounded claim
indices. Unknown model-written keys remain `field`; values, discriminator tags, arbitrary exception text and raw
completions stay excluded. Structured rows additionally report output_tokens and output_cap. Usage at the cap is a
possible truncation signal, not a provider finish reason. The fake truncation test simulates exactly 800 tokens.

Added **intake v5** and **grounding v2**, preserving the old prompt files. Grounding v2 requires precisely the flat
claims/verdict object, string sources, concrete enum values and short claims. It removes the pipe-separated example
value and discourages extra explanations, while retaining the 8-claim/800-token hard caps and strict parsing.
Invalid output still gets one repair, then fails closed; no malformed verdict/source is coerced into support.

Policy number canonicalization supports deterministic English integer spellings through 99, including tens-unit
compounds. It changes no currency conversion or db_tool field/unit checks. Semantic support and units still require
the independent checker. Incorrect periods, unreturned citations, article-authorized prices and DB number/field
mismatches continue to fail closed.

Stock tool `query` is optional and limited to 100 characters and 20 products, mutually exclusive with IDs. Blank,
conflicting and unbounded inputs fail validation. Existing catalogue and inventory permissions/RLS apply; there is
no new permission grant. A query with no matches explicitly asks for clarification, never fabricates zero stock.

## Offline evidence

- Native existing backend Python verified **arm64**; no dependency/environment creation or extension.
- Initial focused red: **3 failed / 20 passed** (missing diagnostic field and the reproduced 30/thirty mismatch).
- First focused implementation: **64 passed in 54.17 s**, including all five KB cases with both number spellings,
  seeded retrieval version=2 assertions, grounding mismatch guards, structured completion and injection fencing.
- Harness/grounding/final-shape group: **125 passed in 175.82 s**, including fake execution through every suite and
  the ordinary-reply final repair/failure and actual-handover server-final paths.
- Final stock/read-tool/intake group: **83 passed in 98.17 s**, including catalogue-only query permission denial,
  blank/conflicting arguments, cross-business isolation and the real-harness chain-stock case without an item-ID
  lookup in the fake generator.
- Complete required backend scope: **1,273 passed in 957.07 s (15m57s)**, exit 0, on the provided local `smb_a0` DB:
  `.venv/bin/python -m pytest -q -p no:cacheprovider app/modules/knowledge app/modules/conversations app/modules/ai app/modules/approvals app/modules/notifications tests`.
- Final Ruff check passes; format check reports **589 files formatted**. Mypy app/evals passes **320 source files**;
  the documented eval/harness scope passes **5 source files**. Scoped `git diff --check` passes.
- Fresh CLI OpenAPI export to a temporary file equals the unchanged task-start tracked schema. No HTTP schema edit,
  OpenAPI file write, migration or frontend edit in this follow-up. Internal tool arguments and eval result fields
  changed as described above.
- Restricted Make check passed: `UV_NO_SYNC=true UV_OFFLINE=true make check -o lint -o typecheck -o test -o deploy-check`
  with a temporary UV cache. Includes **149 fixture rows / 30 tables**, licence checks and secret scan. Backend
  lint/types/tests run separately; frontend and Docker/deployment checks are excluded. This is not an unrestricted
  `make check` claim.
- Final restricted Make check and fresh CLI OpenAPI comparison passed again after the stock lookup changes.
- Independent read-only Tier A review found no blocker in policy normalization, reporting, structured repairs or
  the product-name stock path. Reviewer performed no DB access, tests, edits or external calls.

## Files in this follow-up

```text
backend/app/modules/ai/grounding.py
backend/app/modules/ai/grounding_numbers.py
backend/app/modules/ai/structured.py
backend/app/modules/ai/agent/runner.py
backend/app/modules/ai/tools/{intake.py,inventory.py}
backend/app/modules/ai/prompts/{intake.v5.md,grounding.v2.md}
backend/app/modules/ai/tests/{test_eval_expectations.py,test_intake_followup.py,test_grounding.py,test_knowledge_evals.py,test_intake_agent.py,test_intake_v3.py}
backend/evals/{expectations.py,runner.py,README.md}
backend/evals/cases/intake/ambiguous-customer.yaml
backend/evals/reports/2026-11-05-c1-intake-second-live-fix.md
```

Task-start snapshots: `/private/tmp/p10-c1-livefix2-baseline`. Prior C1 changes, frontend work and supervisor reports
are preserved. No commit, Docker, install, deployment, credentials use or external inference. The supervisor's next
live run remains necessary to measure recovery and obtain the previously missing exact checker/expectation cause.

Prompt SHA-256: intake v5 `8a0d9ddbca11c8f480ac542ec157e72f27a312a94eaa4c263a1447d75440958f`;
grounding v2 `416072aa1beb1022b9c5dbdec6d0b5a79015253a4b2cd7d2b9b255954c8cd6da`.
Tracked OpenAPI SHA-256 remains `c9e92231ebc0ddb91e08bda38ab375a23815b1bf94cd3d142e56729a243e3114`.
