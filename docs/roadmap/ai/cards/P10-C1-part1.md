# P10-C1 part 1 — knowledge base and enforced hand-over

Tier A · EVENT · 2026-11-05 · Backend only. No commit, deployment, installation or external inference.

## Result

Implemented the contract first in [M19](../../modules/M19-knowledge.md) and
[AI contracts](../contracts.md#p10-c1-knowledge-base-and-hand-over), then the backend slice.
Status: **DONE for backend part 1**, with verification limits below. The full P10-C1 workstream remains open for later parts.

Migration: `20261105_1000_knowledge_handoff`, after `20261105_0900_chat_delivery`.

- Visitor hand-over, AI hand-over and direct staff replies pause intake; staff can take over or hand back.
  Inbound messages retain the ownership generation at insertion, so an old queued message stays invalid after
  hand-back. A skipped job records a zero-step outcome without inference or a visitor reply.
- Assigned threads notify their assignee; unassigned threads notify all active users with the send-reply executor's
  approve permission. Visitor hand-over is cookie-scoped, rate-limited and idempotent within a human episode.
  Business-local opening hours select deterministic system copy; closed-hours text is a business setting.
- Public thread/profile responses return AI disclosure text. No widget changes in this part.
- Knowledge CRUD, immutable versions, explicit publication/archive, ranked published full-text retrieval, suggestion
  triage and seed data. Five public Cycle Works articles, four Garden Store articles, one internal guide each.
- Intake v4 retains v3. search_knowledge is public-only for intake; copilot internal retrieval checks current staff
  read permission and recorded run identity. Proposal citations must be article/version pairs returned in this run.
- Empty retrieval + knowledge_missing creates a contact-redacted suggestion from the actual question and requests
  hand-over; other AI hand-overs use request_handoff. The immediate visitor response is a deterministic system message.
  The AI follow-up draft is cancelled along with other open AI replies. No automatic reply is authorized.

## Choices and boundaries

- Staff-requested summary/copilot drafts after takeover remain available for human review, bound to the current
  generation. Background intake remains paused; prior drafts are invalidated.
- Reused conversations.thread.read and owner/admin org.settings.write; no permission bundle or third-party dependency added.
- Publish appends a snapshot rather than updating a draft's published_at, preserving app-role append-only grants.
  Draft edits retain the previous publication. Widening internal→public requires explicit republish.
- PostgreSQL simple configuration, title/body vector with title weight and partial GIN for published snapshots.
  Existing migrations enable btree_gist/pg_trgm/vector, not unaccent; no extension was added.
- Cancellation uses SKIP LOCKED to avoid proposal→thread versus thread→proposal lock inversion. The executor
  checks ownership under the thread lock, even when cleanup skipped its proposal. Durable ownership/handoff events
  retry cleanup; generation checks keep delayed events from cancelling new post-hand-back replies.
- Citation allowlists contain only bounded results actually returned to the model, never results dropped to fit
  observation limits. Tool observations remain untrusted; no retrieved text can register/authorize a tool.
- Suggestions participate in business export and erasure. Articles remain owner-written business content.
- No semantic grounding check, web-chat publication delay, reply autonomy, embeddings or PDF upload (later parts).
  Fake-model injection tests establish fencing, tool restrictions and scripted outcomes; live model resistance remains
  untested here. No TypeSafe integration/API call: this slice uses deterministic checks and the existing inference path.

## Requirement evidence

| Requirement | Tests / evidence |
|---|---|
| Assigned/unassigned notification; visitor idempotency; takeover/hand-back | conversations/tests/test_handoff.py |
| After-hours versus in-hours system message; configurable text; disclosure | test_handoff_business_local_hours_and_configurable_text; visitor response/profile assertions |
| Visitor token and rate limit; direct staff reply ownership | test_staff_reply_takes_over_handoff_requires_token_and_is_rate_limited |
| Queued intake skip; cancel open AI proposal; stale message after hand-back | test_queued_intake_takeover_cancel_and_hand_back |
| Same-thread new visitor message resumes; another manual reply cancels new staff AI draft | test_hand_back_resumes_only_new_visitor_messages; test_manual_reply_in_human_mode_invalidates_new_staff_ai_draft |
| Takeover during inference/safety; no stale reply | test_takeover_during_model_call_cannot_create_reply; test_takeover_after_safety_prevents_agent_call |
| Locked proposal invalidation/executor check; preserve staff proposals | test_locked_reply_is_invalid_at_execution_and_cleanup_preserves_staff_drafts |
| CRUD/version/publish/archive; previous publication retained | knowledge/tests/test_knowledge.py |
| Public/internal/draft/archive filtering, rank/bounds, RLS/permissions, append-only | test_public_visibility_ranking_bounds_and_permissions; test_rls_and_append_only_versions |
| Suggestion redaction/dedupe/dismiss/convert | test_suggestions_redact_dedupe_dismiss_convert |
| Cited warranty; reject unreturned id/version | test_warranty_cited_and_article_text_fenced; test_unreturned_article_or_version_cannot_be_cited |
| No match → safe follow-up + suggestion + hand-over | test_no_match_suggests_actual_redacted_question_and_hands_over |
| Other AI hand-over | test_ai_can_request_handoff_without_creating_a_knowledge_suggestion |
| Hostile article cannot authorize tools; scripted response stays grounded | test_injected_article_cannot_grant_tools_or_discount; kb-injection fake eval |
| Copilot internal visibility/current permission | test_copilot_internal_visibility_requires_current_permission |
| Five new evals: warranty, returns, unknown, DB price, article injection | test_knowledge_evals.py, using the real eval harness and HTTP blocked |
| DAG/route protection/RLS/migration/OpenAPI | tests/architecture, included in the requested command |

## Verification

Native Python: arm64 executable and platform.machine()==arm64; pydantic_core and psycopg_binary extensions arm64.
Existing native /opt/homebrew uv and Node verified. No environment created or extended.

Requested local test database: smb_a0 on localhost:5433, app-role runtime and owner-role migrations.
The exact requested pytest scope passed: **1,102 passed in 791.92 s**.
```sh
cd backend
export TEST_DATABASE_URL='postgresql+psycopg://smb_app:smb_app_dev@localhost:5433/smb_a0'
export TEST_DATABASE_MIGRATION_URL='postgresql+psycopg://smb_owner:smb_owner_dev@localhost:5433/smb_a0'
.venv/bin/python -m pytest -q -p no:cacheprovider app/modules/knowledge app/modules/conversations app/modules/ai app/modules/approvals tests
```
The final outcome-text/status refinement and eval wording alternatives are verified in the focused rerun below.
Ruff check, format check and mypy app pass; OpenAPI was regenerated and compared with a fresh CLI export.
The initial implementation run found 32 related regressions (1,059 passed); fixed payload/version assertions,
added knowledge to existing safety/tenant checks, moved the concurrency barrier before the ownership lock,
and preserved staff-requested summaries after takeover. No failing check was disabled or weakened.
A subsequent all-green run was interrupted at 506 tests to include the repeated-human-reply ownership fix;
the 1,102-pass run above includes that fix.

Initial focused checks: **40 passed in 31.30 s**. Final focused rerun: **55 passed in 30.79 s**
(knowledge, visitor hand-over, all new AI tests and five real-harness FakeGateway evals, plus runner tests).
This rerun includes the final cancelled-draft outcome text/status, subscriber no-enqueue assertion, and both
“thirty”/“30” wording alternatives for correct policy answers.
```sh
.venv/bin/python -m pytest -q -p no:cacheprovider app/modules/knowledge app/modules/conversations/tests/test_handoff.py app/modules/ai/tests/test_knowledge_intake.py app/modules/ai/tests/test_knowledge_evals.py app/modules/ai/tests/test_runner.py
```
Ruff and format check pass; mypy passes (311 source files). OpenAPI regenerated.
Restricted local `make check` passed with `UV_CACHE_DIR=/private/tmp/p10-c1-uv-cache UV_NO_SYNC=true UV_OFFLINE=true`
and `-o test-db -o backend-test -o deploy-check`: lint/types, **347 frontend tests**, license checks and gitleaks
(no leaks). Frontend checks use the existing TypeScript definitions; compatibility after regeneration is untested.
Those flags skip Docker database startup, the Make backend-test target and deployment checks;
this is **not** evidence of an unrestricted full make-check pass. Backend verification is the explicitly requested
local pytest command recorded below. The first restricted attempt hit a sandbox-denied global uv cache; retrying
with the writable temporary cache used the existing native environment without installations.

An additional fixture-validator check found a pre-existing mismatch: fixtures contain autonomy_rules but the
portable SQLite schema lacks that table. Owner clarification requested before changing that unrelated contract;
it remains unchanged pending an answer. Separately loaded all C1 business/article fixture rows against the portable
schema: Cycle public 5/internal 1, Garden public 4/internal 1, foreign-key check clean. This scoped check does not
verify the full fixture-validator check.

Supervisor work: regenerate frontend TypeScript types and run live intake evals. No frontend files changed.

## Files

- New backend knowledge module; conversations ownership, public/staff APIs, models/schemas/repository/service/executor.
- AI knowledge tool, run state/citation checks, inference guard, intake v4, subscribers, copilot catalogue and tests.
- Approvals reply invalidation/public surface; org after-hours setting and greeting; seed and migration.
- data fixtures/SQLite schema, five intake eval cases and eval runtime assertions; backend/openapi.json.
- Contracts/module specs/DAG/index, affected module READMEs, AI handoff, EVENT_WORK and phase status.

Complete changed-path list (including new files):

```text
EVENT_WORK.md
backend/app/modules/ai/README.md
backend/app/modules/ai/agent/runner.py
backend/app/modules/ai/errors.py
backend/app/modules/ai/model_calls.py
backend/app/modules/ai/prompts/intake.v4.md
backend/app/modules/ai/registration.py
backend/app/modules/ai/subscribers.py
backend/app/modules/ai/tests/test_batch_b_regressions.py
backend/app/modules/ai/tests/test_eval_harness.py
backend/app/modules/ai/tests/test_intake.py
backend/app/modules/ai/tests/test_intake_agent.py
backend/app/modules/ai/tests/test_intake_v3.py
backend/app/modules/ai/tests/test_knowledge_evals.py
backend/app/modules/ai/tests/test_knowledge_intake.py
backend/app/modules/ai/tests/test_propose_tools.py
backend/app/modules/ai/tests/test_read_tools.py
backend/app/modules/ai/tests/test_safety_pass_structural.py
backend/app/modules/ai/tests/test_summary.py
backend/app/modules/ai/tools/base.py
backend/app/modules/ai/tools/knowledge.py
backend/app/modules/ai/tools/proposals.py
backend/app/modules/ai/tools/registry.py
backend/app/modules/ai/usecases/copilot.py
backend/app/modules/ai/usecases/intake.py
backend/app/modules/approvals/README.md
backend/app/modules/approvals/invalidation.py
backend/app/modules/approvals/public.py
backend/app/modules/approvals/reading.py
backend/app/modules/conversations/README.md
backend/app/modules/conversations/api.py
backend/app/modules/conversations/executors.py
backend/app/modules/conversations/models.py
backend/app/modules/conversations/ownership.py
backend/app/modules/conversations/public.py
backend/app/modules/conversations/repository.py
backend/app/modules/conversations/schemas.py
backend/app/modules/conversations/service.py
backend/app/modules/conversations/tests/test_conversations.py
backend/app/modules/conversations/tests/test_handoff.py
backend/app/modules/dependencies.py
backend/app/modules/knowledge/README.md
backend/app/modules/knowledge/__init__.py
backend/app/modules/knowledge/api.py
backend/app/modules/knowledge/lifecycle.py
backend/app/modules/knowledge/models.py
backend/app/modules/knowledge/permissions.py
backend/app/modules/knowledge/public.py
backend/app/modules/knowledge/registration.py
backend/app/modules/knowledge/repository.py
backend/app/modules/knowledge/schemas.py
backend/app/modules/knowledge/service.py
backend/app/modules/knowledge/tests/__init__.py
backend/app/modules/knowledge/tests/test_knowledge.py
backend/app/modules/org/README.md
backend/app/modules/org/api.py
backend/app/modules/org/schemas.py
backend/app/seed.py
backend/evals/cases/intake/kb-injection.yaml
backend/evals/cases/intake/kb-price.yaml
backend/evals/cases/intake/kb-returns.yaml
backend/evals/cases/intake/kb-unknown.yaml
backend/evals/cases/intake/kb-warranty.yaml
backend/evals/runtime.py
backend/migrations/versions/20261105_1000_knowledge_handoff.py
backend/openapi.json
backend/tests/integration/test_seed.py
data/README.md
data/fixtures.json
data/schema.sql
docs/roadmap/02-architecture.md
docs/roadmap/13-risks-and-open-questions.md
docs/roadmap/ai/HANDOFF.md
docs/roadmap/ai/cards/P10-C1-part1.md
docs/roadmap/ai/contracts.md
docs/roadmap/modules/M04-conversations.md
docs/roadmap/modules/M13-ai.md
docs/roadmap/modules/M19-knowledge.md
docs/roadmap/modules/README.md
docs/roadmap/phases/STATUS.md
```
