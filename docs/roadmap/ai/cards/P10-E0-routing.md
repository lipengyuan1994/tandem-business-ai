# P10-E0 — local-only regulated routing

Tier A / EVENT, 2026-11-06. Routing half of ADR-020 decision 5; local-model benchmark/deployment are separate.
Status: **DONE — routing half**, verified locally; broader benchmark/deployment separate.

## Behavior and requirement evidence

| Requirement | Implementation and tests |
|---|---|
| Regulated means unhosted/local, BAA grants nothing; strict_private unchanged | `gateway/router.py:policy_allows`; `test_policy_matrix` covers every policy × hosted/vendor/baa, including invalid combinations at the policy boundary. Existing BAA-acceptance test now expects only local; fake no longer bypasses regulated. |
| Old BAA files load and warn at regulated startup | `ProviderCfg.baa` retained; explicit legacy keys warn `ai_routes_baa_ignored` with provider name, including false. `test_legacy_baa_key_loads_and_warns_under_regulated` and actual non-production startup-hook regression. |
| Unhosted with non-local vendor rejected by loader | `RoutesFile` validator names `providers.<name>.vendor`; tests cover vultr/external. Hosted with local vendor still loads but fails regulated policy. |
| Verify local addresses, fail closed | `gateway/local_address.py`, injected resolver and router cache. Tests cover loopback, all RFC1918 ranges, ULA, link-local, Docker/internal names, IPv4-mapped IPv6, public/special/mixed/invalid/empty answers and DNS failure; warning contains host only, never URL credentials/query. Standard/strict_private add no address restriction. |
| Every class/fallback filtered | `test_baa_never_used_even_as_fallback` enumerates all eight classes, including STT/embed/grounding. Transient local failure never falls back to BAA-hosted API. |
| Recheck during run | `ModelCaller.current_policy`, call-scoped `policy_scope`, router attempt and post-result checks; `complete_attempt` guards after budget reservation. Real persisted business-setting and profile-tightening tests cover successive agent steps, structured writer/safety/grounding repairs, fallbacks, in-flight success/timeout/auth/bad_request, and the post-reservation boundary, with/without a local route. Disallowed results are discarded; no allowed route records policy_blocked. |
| Specific availability | `/api/v1/ai/status` retains aggregate codes and adds `model_classes` for all eight classes with enabled/reason (e.g. `regulated: no local route for stt`). Direct policy/partial-route tests and exact HTTP DTO assertion; OpenAPI regenerated. |
| Local example validates | `deploy/config/ai-routes.local.example.yaml`; all eight classes, private hostname, unhosted/local, no keys/prices. Loader test also checks allowed-route eligibility with injected private resolution. |
| Operator guidance | ADR-018 as-built, contracts §6/E0, handoff, AI README, production comments, regulated runbook: private LAN/server, DNS, no port forward, firewall, System profile/attestations and AI status diagnostic check. |

## Files changed

- Backend implementation: `backend/app/modules/ai/gateway/{routes,router,local_address}.py`,
  `backend/app/modules/ai/{model_calls,public,schemas}.py`, `backend/app/modules/ai/agent/runner.py`.
  `backend/app/modules/ai/usecases/copilot.py` delegates allowed-route checks to its inner router, preserving
  injected resolution and cached verification (regression uses a DNS function that fails if called).
- Tests: `backend/app/modules/ai/tests/{test_regulated_routing,test_routing_policy_changes,test_router,test_ai_module,test_safety_pass_boundaries,test_structured}.py`.
- API/module documentation: `backend/openapi.json`, `backend/app/modules/ai/README.md`.
- Deployment configuration: `deploy/config/ai-routes.local.example.yaml`, `deploy/config/ai-routes.example.yaml`,
  `deploy/compose/{env.prod.template,compose.prod.yml}` (comments only in existing configs).
- Roadmap/runbook: `docs/roadmap/03-decisions.md`, `docs/roadmap/ai/{contracts,HANDOFF}.md`, this card,
  `docs/roadmap/phases/{P10-ai-workflows,STATUS}.md`, `docs/runbooks/regulated-profile.md`, `EVENT_WORK.md`.
  Unrelated working-tree edits to AGENTS.md and P10's D5/D7 decisions, and newly appeared local-model eval
  reports under `backend/evals/reports/`, were preserved (author/results not independently verified).
  This task only updates the phase's regulated-routing row.

## Verification

Existing native runtime verified: Python/uv/Node arm64, pydantic_core and psycopg compiled extensions arm64.
No dependency changes, downloads, external provider calls, real keys, Docker, commits or deployment.
TypeSafe calls are unnecessary for deterministic routing rules and excluded by the offline scope.

- Initial failing policy test reproduced the old acceptance of unhosted/non-local metadata (3 passed, 1 failed).
- Focused initial implementation: **164 passed in 18.22 s** before review extensions.
- Initial broad AI run interrupted after **232 passed, 1 failed**: exact status-DTO assertion needed the new field.
  Updated that assertion to verify the complete response; no production behavior was weakened.
- Next broad AI run interrupted after **809 passed, 16 failed**: the new logging assertions reused a bound logger
  cached against a prior app logging configuration. The expected safe warnings appeared in stdout, while
  capture_logs saw no entries. New tests now bind a fresh router logger per test; assertions remain strict.
  The immediate focused restart encountered `org.slug_taken` on its first test after the interruption;
  its normal teardown cleaned the residual test data before retry. No unrelated product change was made.
- Focused final regressions after app logging setup: **201 passed in 22.79 s**, including the copilot wrapper,
  all warning assertions and all mid-run policy-change cases.
- Complete subsequent AI run: **1,020 passed, 3 failed in 886.96 s**. Two more exact budget/status DTO assertions
  required the new field; updated both to assert the full class map while retaining budget/no-call/UTC-reset checks.
  The startup test now clears the preceding tests' router override before testing actual file loading.
  The chained full-backend/static commands correctly did not start after that failed AI result.
- Affected safety/structured/startup/address regressions then passed: **110 passed in 35.17 s**.
- Existing tests changed: `test_policy_regulated_only_baa_or_unhosted_and_forced_by_data_profile` renamed and
  now rejects BAA-hosted; `test_fake_provider_passes_every_policy` renamed and now rejects regulated bypass;
  exact HTTP status assertion includes all class diagnostics; entrypoint denial test now covers baa=false/true.
- Read-only second review found non-transient errors bypassing tightened-policy handling and dev regulated startup
  warnings being deferred. Both fixed and covered; reviewer verified fixes and reported no remaining blockers.
- Final Ruff check/format check passed (606 Python files); mypy app passed (317 source files).
- Backend OpenAPI regenerated with `.venv/bin/python -m app.cli openapi > openapi.json`; only the status
  addition and new class-status schema changed. `git diff --check` passes.
- Restricted `make check` passed: fixtures **149 rows / 30 tables**, backend/frontend lint and types,
  **380 frontend tests / 39 files**, deployment regressions **105 tests, 19 skipped**, licenses and no leaks.
  Invocation (no environment sync/install, Docker, downloads or external calls):

  ```sh
  UV_CACHE_DIR=/private/tmp/p10-e0-uv-cache UV_NO_SYNC=true UV_OFFLINE=true UV_PYTHON_DOWNLOADS=never \
    DEPLOY_CHECK_NO_DOWNLOADS=1 DEPLOY_CHECK_OFFLINE=1 make check -o test-db -o backend-test
  ```

  Docker DB startup and Make's backend-test target were explicitly excluded; backend tests run separately
  against the existing localhost:5433 database. Offline deployment skips live Caddy/Compose resolution,
  Terraform providers and cloud-init runtime/schema checks. This is not an unrestricted make-check pass.
  Final copilot wrapper delegation change was made after this restricted pass; final backend/static checks cover it.
- Final requested AI command passed: **1,023 passed in 917.08 s (0:15:17)**:

  ```sh
  cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider app/modules/ai
  ```

- Full requested backend suite passed: **2,010 passed in 1282.24 s (0:21:22)**. xdist is absent, so the authorized
  sequential fallback was used:

  ```sh
  cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider -x
  .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
  ```

  Final output: `All checks passed!`, `606 files already formatted`,
  `Success: no issues found in 317 source files`. No further implementation changes followed these checks.

## Limits and open questions

No unresolved implementation decision. Broader P10-E0 benchmark/deployment is separate and was not verified here.
This routing task did not probe an actual local model server, clinic network, DNS or modality quality; resolution
is injected in tests. Other local-model eval reports appeared during the run and were left untouched/unassessed.
Address eligibility is cached for each router lifetime and is not proof of clinic control. Operator network/firewall
verification remains required. Already in-flight data cannot be recalled after a policy change.

The existing platform HTTP guard is stricter than route eligibility: it requires configured DNS hosts and refuses
literal-IP URLs/link-local transport. No platform change was authorized/needed; the example uses llm.internal.
STT/embed/TTS share the policy filter but execution adapters remain future work; the gateway currently implements chat.
The System frontend still hard-codes its pre-AI placeholder. This backend-only slice supplies specific diagnostics
through `/ai/status` and documents inspection; rendered class diagnostics and generated frontend types were not changed.
An unrestricted make check would invoke Docker; the restricted invocation and any skipped deployment checks are reported separately.
