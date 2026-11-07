# Implementation roadmap — master index

**This folder is the single source of truth for building Tandem Business AI (formerly SMB Operations Suite).** If code and this roadmap disagree, stop and resolve the disagreement in the roadmap first, then change the code.

- Version: 1.0 (2026-11-03 UTC)
- Owner: Patrick (product owner); every change to decisions goes through [03-decisions.md](03-decisions.md)
- Status legend used everywhere: `PLANNED` → `IN PROGRESS` → `DONE` → `VERIFIED` (verified = acceptance commands were run and passed)

## What we are building, in one paragraph

A production-grade, containerised ERP for small and mid-size businesses. It covers customers (CRM), conversations, scheduling, catalog, inventory and marketing, with sales and purchasing to follow. An AI assistant reads business data, drafts replies and plans multi-step work. It can only *propose* changes. Staff approve proposals in one queue, and deterministic code executes them with a full audit trail. Each customer runs their own copy on their own Vultr account, installed with one script. The first public milestone is the Vultr "Agent Rush" hackathon (Challenge 2, Future of Work, submission due 8 Nov 2026). The goal after that is a pilot-ready v1.

## How to read this roadmap

| If you are… | Read in this order |
|---|---|
| New to the project (human or model) | This page → [01](01-product-and-scope.md) → [02](02-architecture.md) → [12](12-agent-playbook.md) |
| A coding agent given a task ID such as `P3-07` | [12-agent-playbook.md](12-agent-playbook.md) → the task in [phases/](phases/README.md) → only the files the task's **Read first** line lists |
| Working on one module | That module's spec in [modules/](modules/README.md) → [04-repository-and-conventions.md](04-repository-and-conventions.md) |
| Deploying | [10-vultr-infrastructure.md](10-vultr-infrastructure.md) → [11-quality-and-operations.md](11-quality-and-operations.md) |
| Reviewing a decision | [03-decisions.md](03-decisions.md) and [13-risks-and-open-questions.md](13-risks-and-open-questions.md) |

## Document map

| # | Document | Answers |
|---|---|---|
| 01 | [Product and scope](01-product-and-scope.md) | Who it is for, modules, what ships when, non-goals, glossary |
| 02 | [Architecture](02-architecture.md) | Modular monolith, module dependency graph, request/job/event flows |
| 03 | [Decisions (ADRs)](03-decisions.md) | Every technology choice, with the reason and the rejected alternatives |
| 04 | [Repository and conventions](04-repository-and-conventions.md) | Exact folder layout, module template, coding/API/test rules, code patterns |
| 05 | [Data model](05-data-model.md) | Database conventions, tenancy (row-level security), migrations, table index |
| 06 | [Security, identity and privacy](06-security-and-privacy.md) | Login, roles and permissions, sessions, CSRF, secrets, egress, retention |
| 07 | [Workflow engine](07-workflow-engine.md) | Proposals, approvals, executors, jobs, outbox, domain events, audit |
| 08 | [AI layer](08-ai-layer.md) | Inference gateway, model routing (Vultr first), agent loop, tools, evals |
| 09 | [Frontend](09-frontend.md) | Stack, design system, page map, state, accessibility |
| 10 | [Vultr infrastructure and deployment](10-vultr-infrastructure.md) | Vultr services used, topologies, Terraform, containers, backups, $200 budget |
| 11 | [Quality and operations](11-quality-and-operations.md) | Test strategy, gates, observability, release/upgrade, production checklist |
| 12 | [Agent playbook](12-agent-playbook.md) | How humans and models pick up, do and finish a task; proposed AGENTS.md |
| 13 | [Risks and open questions](13-risks-and-open-questions.md) | What could sink us and what is still undecided |
| 14 | [Sources](14-sources.md) | Verified external facts (Vultr, event, models), with dates |
| — | [ai/](ai/README.md) | **AI build kit**: architecture, frozen contracts, integration map, testing, one self-contained card per AI task. Start here for any AI work |
| — | [modules/](modules/README.md) | One spec per module: tables, API, permissions, events, UI, tests |
| — | [phases/](phases/README.md) | Phased task list with IDs, dependencies and acceptance checks; [STATUS.md](phases/STATUS.md) tracks progress |

## The ten rules that keep this buildable

1. **Modules own their tables.** No module reads or writes another module's tables. It calls the other module's `public.py` instead ([02](02-architecture.md)).
2. **Dependencies form a declared graph with no cycles**, checked by a test ([04](04-repository-and-conventions.md#module-boundaries-are-tested)).
3. **Every business row carries `business_id`.** PostgreSQL row-level security enforces it in addition to application checks ([05](05-data-model.md)).
4. **The AI never mutates business data.** It reads through tools and creates proposals. Only executors mutate, and only after approval ([07](07-workflow-engine.md), [08](08-ai-layer.md)).
5. **Executors call the same service functions as the UI.** Business rules live in one place.
6. **Every mutation writes an audit event in the same database transaction.**
7. **External side effects go through the outbox.** They are never retried blindly when the outcome is unknown.
8. **The OpenAPI schema is the contract between backend and frontend.** The TypeScript client is generated from it, never hand-written.
9. **One task touches one module**, apart from explicitly listed integration tasks. A task is done only when `make check` passes and the module spec matches the code.
10. **Anything AI-powered is built after kickoff** (3 Nov 2026, 15:00 UTC). Before then, build only non-AI platform and ERP features, and disclose everything ([phases/README.md](phases/README.md#eligibility-split)).

## Timeline at a glance

| Phase | Dates (2026, UTC) | Outcome |
|---|---|---|
| [P0 Foundation](phases/P0-foundation.md) | Nov 3 | Roadmap approved, organiser question asked, toolchain approved |
| [P1 Platform kernel](phases/P1-platform-kernel.md) | Nov 3 | Repo, dev environment, kernel, module boundaries, CI script |
| [P2 Core services](phases/P2-core-services.md) | Nov 3 | Login, businesses, roles, audit, approvals engine, jobs, outbox |
| [P3 Business modules](phases/P3-business-modules.md) | Nov 3 | CRM, catalog, scheduling, inventory, conversations, Today, UI |
| [P4 Packaging and deployment](phases/P4-packaging-and-deployment.md) | Nov 3 | Images, compose, Terraform, backups; first Vultr deploy after credits arrive |
| [P5 AI layer](phases/P5-ai-layer.md) | Nov 3–6 | Inference gateway, assistant, intake, proposals, evals |
| [P6 Event hardening](phases/P6-event-hardening.md) | Nov 5–7 | Marketing drafts, voice (stretch), end-to-end tests, demo data |
| [P7 Submission](phases/P7-submission.md) | Nov 7–8 | Public repo, demo URL, video, slides (each needs owner approval) |
| [P8 Production v1](phases/P8-production-v1.md) | Nov 9–Dec 20 | Sales, purchasing, files, point-in-time recovery, security review, installer |
| [P9 Pilot](phases/P9-pilot.md) | From Jan 2027 | First real customers on their own Vultr accounts |
