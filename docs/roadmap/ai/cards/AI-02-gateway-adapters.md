# AI-02 · Gateway types, provider adapters, FakeGateway

Kind **EVENT** · Tier A · Size M · Depends on: AI-01, PR-02 (local client) · Blocks: AI-03

## Goal
One small, provider-agnostic way to send a chat request and get text + token usage back, with adapters for
OpenAI-compatible providers (Vultr Serverless Inference, OpenAI, Gemini's compatible endpoint, llama.cpp) and Anthropic,
plus the test double every later card uses.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/contracts.md` §5 (all), §6 (`ProviderCfg`, `RouteEntry.json_schema` only).
2. `docs/roadmap/ai/testing.md` §1, §2.
3. `docs/roadmap/ai/integration-map.md` §A (`app.platform.http.client`).
4. `backend/app/platform/http.py` (how to get the allowlisted client; do not create your own httpx client).
5. Provider request/response formats: OpenAI-compatible `POST /chat/completions` with `response_format`
   (`json_schema` or `json_object`), `usage.prompt_tokens/completion_tokens`; Anthropic `POST /v1/messages`
   (`x-api-key`, `anthropic-version: 2023-06-01`, `usage.input_tokens/output_tokens`). Use your own knowledge of these
   documented formats; do not fetch the web.

## You build
`ai/gateway/types.py`, `openai_compat.py`, `anthropic.py`, `fake.py`, `__init__.py`; fixtures in
`ai/tests/fixtures/`; tests `ai/tests/test_gateway.py`.
Rules: error mapping — timeout → `timeout`; HTTP 429 → `rate_limited` (parse `retry-after`); 5xx → `server_error`;
401/403 → `auth`; other 4xx → `bad_request`; connection refused / egress denied → `unavailable`. Latency measured with
`time.monotonic()`. **Never log or include message content in exceptions**; log provider, model, status code, latency.
Behaviour by `ChatRequest.json_mode` (set by the router, contracts §5): `json_schema` → `response_format` json_schema;
`json_object` → `{"type":"json_object"}` and append `"\n\nReturn JSON matching this schema:\n" + json.dumps(schema)`
to the system message; `text` → no response_format. `OpenAICompatAdapter(name, base_url, api_key, *, local=False)` uses
`app.platform.http.client(local=local)`. Anthropic: system prompt in
`system`, JSON via instruction plus assistant prefill `{` (prepend `{` to the returned text).

## Tests (write first)
`test_openai_compat_parses_text_and_usage`, `test_openai_compat_json_schema_request_body`,
`test_openai_compat_json_object_fallback_appends_schema`, `test_error_mapping_429_5xx_401_timeout_malformed`,
`test_anthropic_parses_and_prefills_json`, `test_no_message_content_in_logs`, `test_fake_gateway_script_and_calls`,
`test_fake_gateway_raises_when_script_exhausted`, `test_egress_denied_maps_to_unavailable`.

## Done when
`make check` passes with no network access needed; `HANDOFF.md` rewritten.

## Out of scope
Choosing models, fallback between providers (AI-03), live calls.
