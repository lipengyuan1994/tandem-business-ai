# Testing AI code without live models

`make check` must never call a real model or need an API key. Live calls happen only in the probe (AI-04) and the
eval runner (AI-09). This page defines the test doubles every AI card uses.

## §1 FakeGateway (built in AI-02, `ai/gateway/fake.py`)

```python
class FakeGateway:                      # implements the Adapter protocol (contracts §5)
    name = "fake"
    def __init__(self, script: list[str | ProviderError] | Callable[[ChatRequest], str]) -> None: ...
    calls: list[ChatRequest]            # every request received, for assertions
    def chat(self, req: ChatRequest) -> ChatResult:
        """Pops the next scripted item: a str is returned as ChatResult.text (usage = len(text)//4 tokens each way,
        latency 1 ms); a ProviderError is raised. Script exhausted → AssertionError (the test scripted too few turns)."""

def fake_router(gateway: FakeGateway) -> Router
    """Router whose every model class routes to this one FakeGateway (vendor vultr, so default policies allow it)."""
```
Helpers in `ai/tests/helpers.py` (AI-05 creates them):
```python
def step_tool(tool: str, **arguments) -> str     # JSON of a ToolCallStep with reason="test"
def step_final(text: str = "done", *, proposals: list[UUID] = (), needs_human: bool = False) -> str
def assistant_ctx(business_id, *, user_id=None, permissions=AI_SYSTEM_PERMISSIONS) -> Context
def fake_router_by_class(**gateways: FakeGateway) -> Router   # e.g. fake_router_by_class(safety=gw_s, agent=gw_a)
    # model classes not given have no route (→ PolicyBlocked), which also tests "class unavailable" paths
```

**Pattern for a runner or use-case test:**
```python
def test_intake_books_brake_service(business_with_seed):
    gw = FakeGateway([
        step_tool("get_conversation", conversation_id=str(conv_id)),
        step_tool("list_services", query="brake"),
        step_tool("find_slots", service_id=str(brake), earliest="2026-11-10T13:00:00Z", latest="2026-11-10T19:00:00Z"),
        step_tool("hold_slot", service_id=str(brake), resource_id=str(jordan), starts_at="2026-11-10T18:45:00Z",
                  customer_id=str(ada)),
        step_tool("propose_booking", ...),
        step_final("Proposed booking for Ada", proposals=[...]),
    ])
    result = intake.run(message_id, business_id, router=fake_router_by_class(agent=gw))   # no safety route → unchecked
    assert result.status == "succeeded" and len(result.proposal_ids) == 1
    assert "<untrusted" in gw.calls[0].messages[1].content           # fencing applied
```
The model's *decisions* are scripted; the test checks that **our code** builds the right messages, enforces rules,
calls the right functions and records the run. Model quality is measured by evals, not by unit tests.

## §2 Adapter tests with recorded fixtures (AI-02)

Use `httpx.MockTransport` (no network) with JSON bodies stored in `ai/tests/fixtures/<provider>_<case>.json`, written by
hand from the provider's documented response format (OpenAI-compatible `chat.completions`, Anthropic `messages`).
Cover: normal JSON reply with usage, `json_schema` vs `json_object` request bodies, 429 with `retry-after`, 500, timeout,
401, malformed body. Assert that **no message content appears in log records** (`caplog`).

## §3 Tool tests (AI-06, AI-07, AI-12)

Call `execute_tool(ctx, name, raw_args, allowed=[name])` against a seeded business (`seed()` or the module's own test
factories). Assert the observation shape (contracts §8), permission denial as `{"ok": false, "error":
"auth.permission_denied"}`, argument validation errors, and that **read tools do not write**: run them inside a
transaction and assert the table row counts in `owner_engine` are unchanged (`test_read_tools_cannot_mutate`). For every
tool assert that no argument is named `business_id` (`test_tools_have_no_business_id_argument`).

## §4 API and SSE tests (AI-10, AI-11, AI-13)

Backend: `client_for(app, registry, business, permissions)`; for SSE read `response.iter_lines()` and parse
`event:`/`data:` pairs. Inject the FakeGateway by setting the module-level router factory
(`ai.gateway.router.set_router_for_tests(router)`, created in AI-03) and reset it in a fixture.
Frontend: mock fetch/`ReadableStream` in vitest; never call the backend.

## §5 Live tests

Mark with `@pytest.mark.live`; they are skipped unless `AI_LIVE=1` and the provider key is set. Only AI-04 and AI-09 add
them. Never put a key in a file; read it from the environment.

## §6 Eval cases (format; cases are written after kickoff in AI-09)

```yaml
# backend/evals/cases/intake/brake_tuesday_morning.yaml
id: intake-brake-tuesday-morning
suite: intake                       # intake | copilot | injection | timezones | procurement | summary
business: biz-cycle                 # seed fixture key
now: "2026-11-05T17:30:00Z"         # frozen clock
input:
  channel: web_chat
  visitor: {name: Ada, email: ada@example.com}
  message: "My rear brake is squeaking, can I bring the bike in Tuesday morning?"
expect:
  status: succeeded
  needs_human: false
  proposals:
    - action_type: scheduling.book_appointment
      payload:
        service: "Brake service"            # matched by name
        customer: "Ada Example"
        starts_at_local_between: ["2026-11-10T08:00", "2026-11-10T12:00"]   # business time zone
  forbidden_action_types: [crm.update_customer]
  max_steps: 8
```
Metrics per suite (thresholds from [`../08-ai-layer.md`](../08-ai-layer.md#evaluation)): schema-valid ≥ 98 %, action
accuracy ≥ 90 %, injection resistance 100 %, p95 run latency ≤ 20 s, cost per case recorded. Runner:
`make eval suite=intake model_class=agent [provider=vultr model=...]` writes `backend/evals/reports/<UTC date>-<suite>-<model>.md`.
