# Phases and tasks

Work happens in phases. Each phase has tasks with stable IDs (`P3-07`) and an **exit gate**. Progress is tracked in [STATUS.md](STATUS.md). Agents follow [12-agent-playbook.md](../12-agent-playbook.md).

## Calendar (2026, UTC)

```
Oct  25 26 27 28 29 30 31 | Nov 1  2  3  4  5  6  7  8 | Nov 9 ──────────── Dec 20 | Jan →
P0 ██                      |                           |                          |
P1    ███████              |                           |                          |
P2          ██████         |                           |                          |
P3             ████████████|██ ██                      |                          |
P4                         |██ ██ ▲█                   |                          |   ▲ = first Vultr deploy (credits)
P5                         |      ██ ██ ██ ██          |                          |   EVENT: AI work starts 3 Nov 15:00
P6                         |            ██ ██ ██       |                          |
P7                         |                  ██ ██    |   submission 8 Nov 22:00 |
P8                         |                           | █████████████████████████|
P9                         |                           |                          | ████
```

| Phase | Dates | Kind | Exit gate summary |
|---|---|---|---|
| [P0 Foundation](P0-foundation.md) | Nov 3 | GATE (owner) | Roadmap approved; organisers asked; tools and AGENTS.md approved |
| [P1 Platform kernel](P1-platform-kernel.md) | Nov 3 | PRE | `make dev` and `make check` green; architecture tests in place |
| [P2 Core services](P2-core-services.md) | Nov 3 | PRE | Login with MFA, roles, audit, approvals engine with a fake executor end to end |
| [P3 Business modules](P3-business-modules.md) | Nov 3 | PRE | Flagship workflow works without AI (staff-created proposal); e2e green |
| [P4 Packaging and deployment](P4-packaging-and-deployment.md) | Nov 3 | PRE (+ deploy on Nov 3) | Production compose rehearsed locally; demo live on Vultr after credits |
| [P5 AI layer](P5-ai-layer.md) | Nov 3–6 | **EVENT** | Intake and copilot meet eval thresholds; injection suite 100 % |
| [P6 Event hardening](P6-event-hardening.md) | Nov 5–7 | EVENT / PRE | Demo script runs on the public URL; security pass; restore drill |
| [P7 Submission](P7-submission.md) | Nov 7–8 | GATE (owner) | Submitted before 22:00 UTC on 8 Nov |
| [P8 Production v1](P8-production-v1.md) | Nov 9–Dec 20 | — | Production readiness checklist ([11](../11-quality-and-operations.md#production-readiness-checklist-p8-exit-gate-before-any-real-customer-data)) |
| [P9 Pilot](P9-pilot.md) | Jan 2027 → | — | First customers live with support and legal review |

Overlaps are deliberate. Tasks in different modules run in parallel once their dependencies are done. A good parallel plan for three agents in P3: one on scheduling + inventory (Tier A), one on crm + catalog + conversations backend (Tier B), one on frontend pages (Tier B).

## Eligibility split

| Kind | Meaning | Allowed |
|---|---|---|
| `GATE` | A decision or action by the owner (approve, ask, publish, pay) | Owner only |
| `DOC` | Documentation or synthetic data only | Any time |
| `PRE` | Non-AI code: platform, ERP modules, UI, packaging, tests | From P0 gate (owner adopts the new AGENTS.md) |
| `EVENT` | AI-powered functionality: inference, prompts, agent, tools, evals, AI UI, voice | **Only after 2026-11-03 15:00 UTC** |
| `DEPLOY` | Creates or changes cloud resources | Only with owner approval in chat, from 3 Nov (credits) |

Every PRE task finished before kickoff gets a line in `PRE_EVENT_WORK.md` (task ID, date, summary).

## Task block format

```
### P3-04 · Scheduling: resources, availability and slot finder
Kind: PRE · Tier: A · Module: M06 scheduling · Size: L · Depends on: P2-01, P3-01
Read first: modules/M06-scheduling.md, 04-repository-and-conventions.md, 05-data-model.md
Create/modify: backend/app/modules/scheduling/…, backend/migrations/versions/…
Steps:
  1. …
Done when:
  - [ ] `make check` passes
  - [ ] tests named X, Y exist and pass
```

**Size:** S ≈ under 2 hours of agent time, M ≈ half a day, L ≈ a day. Split anything bigger.
