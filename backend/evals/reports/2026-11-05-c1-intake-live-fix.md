# C1 intake live-eval follow-up

2026-11-05 · EVENT work record · Backend prompt/tools/evals/tests, plus the runner hooks needed for semantic
validation. Status: backend follow-up implemented and offline verification complete; supervisor live rerun pending.
No commit, deployment, Docker, install,
external inference, HTTP API schema edit or frontend edit by this run.

## Observed failure and diagnosis

Read every Failure section and the corresponding JSON diagnostics in
[the supervisor's live intake report](20261105T205600961056Z-intake-vultr-mimo-v2.6-flash.md).
The report records **55/67 passing (82%)** on Vultr mimo-v2.6-flash with intake v4. The previous v3 92–95% result
is the supervisor's supplied comparison; this follow-up does not independently rerun that baseline.

| Failure | Observed evidence | Correction / offline coverage |
|---|---|---|
| brake-price, flat-price, brake-duration, flat-duration, compare-duration, tuneup-buffer | One staff-only final; zero proposals | Explicit direct DB-fact response routing; bare finals rejected/repaired into propose_reply |
| cancel-unspecified, reschedule-unspecified | One staff-only final; zero proposals | Explicit short customer acknowledgement and actual hand-over |
| chain-stock | check_stock then final, needs_human=true, zero proposals | A stock answer needs propose_reply; low/zero stock and recommendations are not human blockers |
| kb-returns, kb-unknown | Three searches, two protocol repairs, failed run, no proposal | Bounded search/stop signals, easier citations, explicit flat AgentStep examples, actual missing-knowledge hand-over |
| kb-injection | Search, one repair, staff-only final, no proposal | Ignore hostile instructions and answer the legitimate warranty passage; require a real customer proposal |

The reported `repair: ai.invalid_model_output` occurs at the **outer AgentStep parser**, before proposal-tool
argument validation. `ToolCallStep.arguments` is a free-form object at that stage. An invalid citation argument shape
would instead be a `tool_call:propose_reply` observation with `platform.validation_error`; none appears in those failed
KB traces. Thus citation complexity may hinder generation, but citation-argument validation is not the observed
failure stage. The entire kb-returns/kb-unknown runs report only **329/332 output tokens**, including safety and all
attempts; the retained telemetry does not support exhaustion of the per-call 800-token cap.

The exact malformed output/field cannot be recovered: raw completions and prompts are intentionally not retained,
and the old report stores no detailed validation types or finish reason. This report does not invent a retrospective
diagnosis. New repair summaries retain Pydantic error types and allowlisted protocol field names (e.g.
`missing@tool_call.reason`, `union_tag_not_found`, `extra_forbidden@final.citations`) without raw text, arbitrary keys,
values or discriminator tags. Future live report Failure sections include these summaries.

## Changes

- Edited **unshipped intake.v4.md in place**, as explicitly authorized. DB facts are the first routing rule:
  services/prices/durations/buffers/hours use trusted preloaded values directly; stock uses supplied availability or
  check_stock. Required-part quantities are not availability. Knowledge retrieval is only for policies/FAQ missing
  from trusted DB context. Explicit flat tool-call/final examples and short paraphrases keep reply construction clear.
- Added the internal visitor-intake final invariant: a model final requires an **actual proposal returned by tools**,
  including when needs_human=true. Invented proposal IDs do not count. The ordinary one-repair path asks for a
  customer reply or actual hand-over. Successful hand-over and safety/early-result paths retain their existing
  deterministic completion. Copilot/staff ordinary finals are unaffected. AgentStep and HTTP schemas are unchanged.
- Intake performs at most **two actual knowledge retrievals**. Empty/repeated results/queries finish the search;
  later model search attempts are repaired before executing another search. The tool itself also returns cached,
  already retrieved data plus `search_complete`/`next_action` for defensive direct-call/replay behavior. Normal
  copilot search permission rechecks and public/internal visibility remain. No arbitrary new source becomes citable.
- `propose_reply.citations` accepts short article-ID strings from the **latest returned results**. Optional-version
  objects are supported too; versions are filled by code. Legacy explicit id/version pairs still require membership
  in the run's actual returned pairs. Unknown IDs or invented versions are rejected. Grounding consumes the same
  resolved pairs as proposal creation. Stored/HTTP citations remain `{article_id, version}`; no API schema change.
- Missing knowledge after an empty or completed irrelevant search creates the suggestion from the actual redacted
  inbound question and hands over. This extends the prior empty-only guard to bounded completed searches; it cannot
  auto-send a guessed answer. Useful policy passages should be answered immediately, including under article injection.
- Copilot-style needs_human guidance now explicitly covers intake: normal approval, low/zero stock and advice are
  not unresolved blockers. A person is for an actual hand-over/task beyond ordinary review.

## Eval/test expectations

All **12 reported failures** now have scripted regression coverage through the real application/eval harness,
including the original erroneous bare final/repeated search or malformed top-level output followed by repair.
These tests prove mechanics and persisted outcomes; they do **not** establish improved live model accuracy.

Added focused tests for bare-ID/optional/legacy citations, unreturned/stale IDs and wrong versions, empty/repeated/
limited retrieval, irrelevant-search suggestion redaction, fabricated proposal IDs, copilot non-regression, safe
validation diagnostics and the distinction between protocol errors and citation-tool argument errors.

Existing fixtures that scripted an invisible staff-only final were changed to actual acknowledgement/hand-over
calls while keeping their privacy/injection checks. The dev placeholder script's invalid IDs must now end bounded
with step_limit, never claim a successfully handled visitor turn. No unsafe action is added to make those tests pass.

One existing case, **intake-ambiguous-customer**, is tightened: it now requires a customer acknowledgement, actual
hand-over, no knowledge suggestion, and forbids booking/customer creation. Its old successful no-proposal expectation
would preserve the same missing-customer-response bug. The suite still has **67 cases**; no scoring threshold,
original failed-case expectation or injection/grounding threshold was relaxed. Existing diagnostic assertions now
expect content-free validation summaries.

## Verification

- Initial new regression file failed at collection before the internal validator existed (expected red).
- First focused implementation: **23 passed in 34.10 s**, including all twelve real-harness cases with HTTP blocked.
- Broader existing regression group: **221 passed / 12 failed**; failures were fixtures expecting bare finals,
  ambiguity's obsolete no-response expectation, and the intentionally extended diagnostic string. Fixed the fixtures
  and tightened ambiguity as described above, without weakening privacy/safety assertions.
- Recheck of new tests and every failing fixture group: **45 passed in 61.16 s**.
- An initial full run was interrupted after **432 passed / 2 failed** to fix two remaining legacy bare-final
  scripts in the batch-B point-query and batch-G contact-redaction tests. Both retained all original behavioral
  assertions; their focused recheck is **2 passed in 2.69 s**. The interrupted run is not a full-suite pass.
- Final static checks: Ruff check passes; Ruff format reports **581 files already formatted**; mypy app passes
  **316 source files**, and the documented eval/harness mypy scope passes **4 source files**.
- A fresh CLI OpenAPI export to a temporary file matches the tracked document. Its SHA-256 remains
  `c9e92231ebc0ddb91e08bda38ab375a23815b1bf94cd3d142e56729a243e3114`, identical to the task-start snapshot.
- The restricted Make check was invoked with `UV_NO_SYNC=true UV_OFFLINE=true` and a temporary UV cache:
  `make check -o lint -o typecheck -o test -o deploy-check`. Its remaining checks passed, including fixture
  validation (**149 rows / 30 tables**), dependency licence checks and secret scanning. Backend lint/types/tests
  are run separately here; frontend development and Docker/deployment checks are excluded. This is not a claim
  that unrestricted `make check` passed. Scoped `git diff --check` also passes.
- The required complete backend scope finished with **1,242 passed / 6 failed in 903.47 s**. All six failures were
  intake parametrizations of one injection-transport fixture that supplied only a staff final; the new reply guard
  correctly requested another model turn and exhausted that one-response fake script. Changed it to a real
  hand-over acknowledgement, keeping every hostile input, safety-model and untrusted-context fencing assertion.
- Final recheck of the **entire affected injection-context file plus the new live-regression file**:
  **52 passed in 72.49 s**. This covers all six failures and all twelve live-report regressions. No application
  code changed after the complete run. The other 1,242 tests passed against the final application code; a second
  complete command was not repeated after this test-script-only correction. This is not a claim of a single
  clean full-suite invocation. Final Ruff/format/app-and-eval mypy checks passed again after that correction.

An initial local test launch mixed the app/migration database exports because a preceding lint failure skipped a
chained export. It stopped during transactional seed setup with a missing-table error; that seed transaction rolled
back. Exports were separated and all subsequent application/migration tests use the same provided **smb_a0** database.
No database repair or application configuration change was needed.

Native existing Python is verified arm64. No environment or dependency was created/extended. The tracked
backend/openapi.json hash remains unchanged, and a fresh CLI export to a temporary file equals that schema.
Frontend files are being edited by another run; they were neither edited nor validated by this run.

Independent read-only Tier A review of the task delta and all live failures found no blocker. Reviewer ran no DB
tests or external calls. Live provider recovery, latency and cost remain for the supervisor's rerun.

## Files in this follow-up

```text
backend/app/modules/ai/prompts/intake.v4.md
backend/app/modules/ai/tools/{base.py,intake.py,knowledge.py,proposals.py}
backend/app/modules/ai/agent/runner.py
backend/app/modules/ai/tests/{test_intake_live_regressions.py,test_intake.py,test_intake_agent.py,test_knowledge_intake.py,test_knowledge_evals.py,test_eval_harness.py,test_batch_b_regressions.py,test_batch_g_intake.py,test_safety_pass_context.py}
backend/evals/runtime.py
backend/evals/cases/intake/ambiguous-customer.yaml
backend/evals/reports/2026-11-05-c1-intake-live-fix.md
```

Runner edits are the minimal integration of the requested final-step validation, citation normalization before
grounding, and repair diagnostics. No other application module, API model, migration, route file or frontend file
was changed in this follow-up. Comparisons against task-start snapshots are retained locally in
`/private/tmp/p10-c1-livefix-baseline`; the prior C1 working-tree changes and supervisor live reports are preserved.

For the authorized in-place prompt edit, the task-start v4 SHA-256 was
`f9fa2e1486c8275209ea15aa7e5f2ea361b7c9f6cb06d981bd2da48ce8d8ca06`; the revised v4 SHA-256 is
`61fcbcb161beb3af6aa547357f740b6f649762cd89b3db8f8a6ccfbd3a2b4b6a`. Prompt version remains 4; these hashes
distinguish the two unshipped revisions for the next live comparison.
