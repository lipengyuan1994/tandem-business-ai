# AI build kit — start here

This folder is everything a coding model needs to build the AI layer (module `ai`, M13) **one task at a time, without
holding the whole project in its context window**. It was written before kickoff (DOC work, owner-approved 2026-11-03).
No AI code, prompt text or evaluation case exists yet: those are built after **2026-11-03 15:00 UTC** by following the
cards below.

## The rule of the kit

**Read only what your task card lists.** Every card names its *context pack*: a short, ordered list of files (and
sections) that together are enough to finish the task. If you feel you need something else, the card is wrong or the
task is too big — stop and ask (see [Stop rules](#stop-rules)). Do not browse the repository "to get a feel for it".

## Files in this kit

| File | What it is | When you read it |
|---|---|---|
| `README.md` (this file) | How to use the kit, task index, order, stop rules | Always, first |
| [`HANDOFF.md`](HANDOFF.md) | Current state of the AI build: what exists, what changed, what is next. Rewritten after every task | Always, second |
| [`architecture.md`](architecture.md) | How the pieces fit: components, run lifecycle, sequences, failure handling | When your card lists it |
| [`contracts.md`](contracts.md) | **Frozen** interfaces: package layout, Python types and signatures, tables, settings, permissions, API, SSE, errors | Only the sections your card lists |
| [`integration-map.md`](integration-map.md) | The **existing** functions, executors, events and test fixtures the AI layer calls, with exact signatures | Only the sections your card lists |
| [`testing.md`](testing.md) | How to test AI code without live models (FakeGateway, scripted runs, recorded fixtures) and the eval format | Every card that writes tests |
| [`cards/`](cards/) | One file per task. Each is self-contained | Your card only |

Outside this folder, cards may point at: `AGENTS.md`, `docs/roadmap/12-agent-playbook.md` (the general task loop),
`docs/roadmap/04-repository-and-conventions.md` (code patterns) and specific code files. The longer design notes in
[`../08-ai-layer.md`](../08-ai-layer.md) and [`../modules/M13-ai.md`](../modules/M13-ai.md) are background; **where
they differ from `contracts.md`, `contracts.md` wins.**

## Task index

`Kind`: **PRE** may be built before kickoff (non-AI ERP code); **EVENT** only after 2026-11-03 15:00 UTC.
`Tier`: A = strongest model + second-model review; B = mid-tier model + spot review ([12](../12-agent-playbook.md#model-tiers)).

| Card | Title | Kind | Tier | Size | Depends on | Roadmap task |
|---|---|---|---|---|---|---|
| [PR-00](cards/PR-00-parallel-workspaces.md) | Parallel agent workspaces (one test database per agent) | PRE | B | S | — | tooling |
| [PR-01](cards/PR-01-tool-read-functions.md) | Read functions and executors the tools need (dashboard summary, overdue invoices, payment reminder) | PRE | B | M | — | P5 prep |
| [PR-02](cards/PR-02-platform-prerequisites.md) | Platform prerequisites: non-transactional jobs, per-business crons, assistant self-approval, hold binding, message context, open-proposal lookup, local inference transport | PRE | A | M | — | P5 prep |
| [PQ-01](cards/PQ-01-procurement-groundwork-backend.md) | Procurement groundwork: quote requests, supplier quotes, comparison, late orders (backend) | PRE | A | L | PR-02 | U9 prep |
| [PQ-02](cards/PQ-02-procurement-groundwork-frontend.md) | Procurement groundwork screens | PRE | B | M | PQ-01 | U9 prep |
| [AI-01](cards/AI-01-module-skeleton.md) | `ai` module skeleton: tables, settings, permissions, registration, tool framework | EVENT | A | M | kickoff, PR-02 | P5-04 |
| [AI-02](cards/AI-02-gateway-adapters.md) | Gateway types, OpenAI-compatible and Anthropic adapters, FakeGateway | EVENT | A | M | AI-01, PR-02 | P5-01 |
| [AI-03](cards/AI-03-router-and-policy.md) | Router, route file, policies (incl. `regulated`), pricing | EVENT | A | M | AI-02 | P5-01 |
| [AI-04](cards/AI-04-probe.md) | Live catalog probe and first route choice | EVENT | B | S | AI-03, API key | P5-01 |
| [AI-05](cards/AI-05-runner.md) | Agent protocol, runner, contexts, budgets, run recording, run/usage API | EVENT | A | L | AI-03 | P5-04 |
| [AI-06](cards/AI-06-read-tools.md) | Read tools | EVENT | A | M | AI-01 | P5-05 |
| [AI-07](cards/AI-07-propose-tools.md) | Propose and hold tools | EVENT | A | M | AI-06, PR-02 | P5-05 |
| [AI-08](cards/AI-08-intake.md) | U1 Intake part 1: trigger, safety, matching, guards, notes | EVENT | A | M | AI-05, PR-02 | P5-06 |
| [AI-08B](cards/AI-08B-intake-agent.md) | U1 Intake part 2: agent run and prompt | EVENT | A | M | AI-08, AI-07 | P5-06 |
| [AI-09](cards/AI-09-evals.md) | Eval harness, cases, model selection | EVENT | A | L | AI-05; suites need AI-08B/10/11/12 | P5-03 |
| [AI-10](cards/AI-10-copilot.md) | U2 Copilot chat over SSE | EVENT | A | M | AI-05, AI-07 | P5-07 |
| [AI-11](cards/AI-11-single-shot-writers.md) | U3 summary + reply, U4 reorder note, U6 briefing | EVENT | B | M | AI-05, AI-07, PR-01 | P5-08 |
| [AI-12](cards/AI-12-procurement-agent.md) | U9 Procurement agent: request quotes, read replies, compare, propose PO, chase late orders | EVENT | A | L | AI-05, AI-07, AI-11, PQ-01 | new |
| [AI-13](cards/AI-13-frontend.md) | Frontend AI features | EVENT | B | L | AI-01 (mocks); integrate after AI-08B/10/11/12 | P5-09 |
| [AI-14](cards/AI-14-safety-pass.md) | Safety pass: screening, injection suite, budgets end to end, log canaries | EVENT | A | M | AI-08B | P5-10 |
| [AI-15](cards/AI-15-campaign-draft-and-payment-followup.md) | U5 campaign draft; U10 overdue payment follow-up (optional) | EVENT | B | S | AI-11, PR-01 | P6-02 |

Stretch tasks stay where they are: voice (P6-03, [M14](../modules/M14-voice.md)) and the CPU benchmark (P5-02).

## Order and parallel tracks

```
PRE (before kickoff):   PR-00   PR-01   PR-02 ──► PQ-01 ──► PQ-02
EVENT (after kickoff, all need PR-02):
  AI-01 ──► AI-02 ──► AI-03 ──┬──► AI-04 (needs the key)
     │                        └──► AI-05 ──┬──► AI-08 ──► AI-08B ──► AI-14
     │                                     ├──► AI-10 (also AI-07)
     ├──► AI-06 ──► AI-07 ─────────────────┼──► AI-11 (also PR-01) ──► AI-15
     │                                     └──► AI-12 (also AI-11, PQ-01)
     └──► AI-13 (against mocked API; integrate when backends land)
  AI-09: harness after AI-05; each suite after its use case
```

Three agents can work at once after AI-01: **track 1** gateway → router → runner → intake; **track 2** tools
(AI-06, AI-07) then procurement; **track 3** frontend (AI-13). Each parallel agent works in its own git worktree and its
**own test database** (card PR-00): agents sharing one test database break each other's tests.

## How to work a card (context-managed loop)

1. **Load:** this README → `HANDOFF.md` → your card → the card's context pack, in that order. Nothing else.
2. **Mark** the card's row in [`../phases/STATUS.md`](../phases/STATUS.md) `IN PROGRESS — <agent> <UTC time>`.
3. **Plan** the files you will touch; they must match the card's *You build* list.
4. **Tests first**, from the card's *Tests* list, using [`testing.md`](testing.md). No live model calls in tests.
5. **Implement** to the interfaces in `contracts.md` exactly (names, types, fields, error codes).
6. **Verify** with the card's *Done when* commands and `make check`.
7. **Hand off:** rewrite `HANDOFF.md` (rules in that file), set STATUS to `DONE`, and report with the template in
   [12-agent-playbook](../12-agent-playbook.md#report-template).

**Checkpoint when your context is about two-thirds full**, or before any long command: write a *Progress* entry at the
bottom of your card (files done, files left, the next concrete step, anything surprising). A fresh session can then
resume from the card and its Progress entry alone. Delete the Progress entry when the card is done.

## Stop rules

Stop, write the question into your card's Progress entry and into
[13-risks-and-open-questions](../13-risks-and-open-questions.md#open-questions), and end the session when:
- you would need to change anything marked **frozen** in `contracts.md` (only a Tier-A task with owner approval may);
- a function in `integration-map.md` does not exist or has a different signature (the map is stale: say which);
- you need to read another module's internals, add a dependency, or call a live model from a test;
- an EVENT card is opened before 2026-11-03 15:00 UTC.
