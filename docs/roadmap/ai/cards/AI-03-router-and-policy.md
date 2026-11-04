# AI-03 · Router, route file, policies, pricing, fake provider

Kind **EVENT** · Tier A · Size M · Depends on: AI-02 · Blocks: AI-04, AI-05

## Goal
Pick a provider and model for each *model class* from a configuration file, honouring the business's privacy policy
(including `regulated`, which allows only providers with a signed BAA), fall back on transient errors, and compute cost.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/contracts.md` §2, §6 (all), §15.
2. `docs/roadmap/ai/architecture.md` §8 (policy table).
3. `docs/roadmap/08-ai-layer.md` section "Providers and model classes" (candidate models for the example file).
4. Your AI-02 files `ai/gateway/types.py`, `fake.py`.
5. `deploy/compose/compose.prod.yml` and `compose.dev.yml`, services `api` and `worker` only (to mount `deploy/config`).

## You build
- `ai/gateway/routes.py` (load + validate the YAML, clear error naming the bad key), `router.py` (`Router` with
  `complete(...) -> tuple[ChatResult, RouteEntry]` that fills `model` and `json_mode` per entry, `PolicyBlocked`,
  `load_router() -> Router | None` cached per process, `reset_router_cache()`, `set_router_for_tests(router|None)`),
  `pricing.py` (`cost_micros`). Providers with `hosted: false` get `OpenAICompatAdapter(..., local=True)`.
- Provider kind `fake` (contracts §2, §6): adapter that replays `AI_FAKE_SCRIPT_FILE` per use case, chosen by
  `ChatRequest.use_case` (contracts §5); refuse to build it when `APP_ENV=prod` (raise at startup).
- `deploy/config/ai-routes.example.yaml` with every provider of the 08 table and placeholder routes; mount
  `deploy/config/` read-only at `/config` in the api and worker services (dev and prod), and add
  `deploy/config/ai-routes.dev.yaml` using the `fake` provider plus `deploy/config/ai-fake-script.dev.yaml` with a
  harmless scripted reply per use case so the dev stack shows AI screens without a key.
- `ai.public.status` now: `enabled` false with `ai.disabled_by_policy` when `router.allowed("agent", policy)` is empty.
- **Production wiring:** forward `AI_ROUTES_FILE`, the four provider key variables, `LOCAL_INFERENCE_HOSTS` and
  `AI_DAILY_TOKEN_BUDGET_PER_BUSINESS` into the api and worker services in `compose.prod.yml` (explicit `environment`
  mapping, like the existing variables); add them to `deploy/compose/env.prod.template` with empty values and comments;
  make `deploy/scripts/install.sh` and `upgrade.sh` copy `deploy/config/` to the install directory (never overwrite an
  existing `ai-routes.yaml`); add a smoke check in `deploy/scripts/smoke.sh` that `GET /api/v1/ai/status` answers.
  Provider hosts go into the `EGRESS_ALLOWLIST` comment (not the value).

## Tests (write first)
`test_policy_hosted_vultr_allows_only_vultr_and_local`, `test_policy_strict_private_only_unhosted`,
`test_policy_regulated_only_baa_or_unhosted_and_forced_by_data_profile`, `test_missing_api_key_skips_provider`,
`test_fallback_on_timeout_and_429_but_not_on_bad_request`, `test_all_routes_failed_raises_unavailable`,
`test_no_allowed_route_raises_policy_blocked`, `test_cost_micros_rounding`, `test_routes_file_validation_errors_name_key`,
`test_fake_provider_refused_in_prod`, `test_example_and_dev_route_files_validate`, `test_json_mode_chosen_from_route_entry`,
`test_load_router_none_without_file`, `test_local_provider_uses_local_client`.

## Done when
`make check` passes; `make check` (including the deploy checks) passes, `docker compose -f deploy/compose/compose.prod.yml config -q` passes,
`make rehearse` passes; dev stack starts and
`GET /api/v1/ai/status` returns enabled with the dev route file; `HANDOFF.md` rewritten.

## Out of scope
Live calls (AI-04), choosing the real winners (AI-09).
