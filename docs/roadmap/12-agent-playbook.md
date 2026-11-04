# 12 · Agent playbook — how any model (or person) builds this

The roadmap is written so a **mid-tier coding model can finish one task with only that task's context**. This page tells the agent exactly how.

## Model tiers

| Tier | Use for | Examples of suitable agents | Review |
|---|---|---|---|
| **A** | Platform kernel, authentication, row-level security, approvals engine, jobs and outbox, executors that touch several modules, AI runner and tools, security-relevant changes, Terraform | Strongest available model (e.g. Claude Opus, GPT top tier) | Human owner reviews the diff; a second model reviews it (`/code-review`, `codex-review`) |
| **B** | Module CRUD following the template, frontend pages, forms, tests, seed data, runbooks, eval cases | Mid or low-cost models (e.g. Sonnet, Haiku, GPT Luna, Gemini Flash-class) | `make check` + a Tier-A model spot review at each phase gate |

Each task in [phases/](phases/README.md) states its tier.

## The task loop

1. **Pick.** Open [phases/STATUS.md](phases/STATUS.md). Take the first `PLANNED` task whose dependencies are all `DONE` or `VERIFIED`, whose tier matches you, and whose kind is allowed today (`PRE` any time; `EVENT` only after 2026-11-03 15:00 UTC; `GATE` tasks are for the owner). Mark it `IN PROGRESS — <agent> <UTC date>`.
2. **Load context, and only this:**
   - the task block in its phase file;
   - every document in its **Read first** line;
   - the `public.py` (and module `README.md`) of each module the task's module depends on, **not their internals**;
   - the existing files of the module you are changing.
   If that does not fit in your context window, the task is too big: stop and ask for it to be split.
3. **Plan.** Write down the files you will create or modify. If the list touches a module other than the task's module (except `public.py` additions explicitly named in the task), stop and ask.
4. **Test first** for business rules: write the failing tests named in the module spec's *Tests* section.
5. **Implement** using the code patterns in [04](04-repository-and-conventions.md#code-patterns-copy-these-shapes). Do not invent new patterns.
6. **Verify.** Run every command in the task's **Done when** list plus `make check`. Fix until green. Never skip, disable or weaken a test to pass.
7. **Document.** Update the module's `README.md` card. If the implementation had to differ from the spec, update the spec **and** list the change under "Spec changes" in your report. Changes to an ADR need owner approval, so propose them instead of making them.
8. **Report** using the template below, and set the task to `DONE` in STATUS.md. A reviewer sets `VERIFIED` after re-running the Done-when commands.

## Stop and ask (do not guess) when…

- The spec contradicts itself, another spec or the code.
- You need a new third-party dependency, service or container.
- You need to read or change another module's internals.
- A test unrelated to your change fails.
- The change affects authentication, permissions, row-level security, audit, the approvals engine or the outbox, and you are not Tier A.
- You are asked to deploy, spend money, create cloud resources, push to a remote or use real credentials.
- The task is EVENT-kind and it is before kickoff.

Write the question in your report and in [13-risks-and-open-questions.md](13-risks-and-open-questions.md#open-questions), choose nothing, and stop.

## Handing a task to a model (prompt template)

```text
You are implementing task {TASK_ID} of the SMB Operations Suite.
Rules: follow /AGENTS.md and docs/roadmap/12-agent-playbook.md exactly.
1. Read: docs/roadmap/phases/{PHASE_FILE} (task {TASK_ID} only), then every file in its "Read first" line,
   then app/modules/<dep>/public.py for each dependency module.
2. Do only what the task says. Touch only the files it names or the module it names.
3. Write tests first for every rule listed. Use the code patterns in docs/roadmap/04-repository-and-conventions.md.
4. Run the task's "Done when" commands and `make check` until all pass. Do not weaken tests.
5. Update the module README.md card and docs/roadmap/phases/STATUS.md.
6. Reply with the report template from 12-agent-playbook.md.
If anything is ambiguous or needs another module's internals, stop and ask instead of guessing.
```

## Report template

```markdown
## {TASK_ID} report
- Status: DONE | BLOCKED (reason)
- Files changed: …
- Tests added: … (names)
- `make check`: <paste the final summary lines>
- Done-when results: <each command → result>
- Spec changes: none | <what and why>
- Follow-ups / questions: …
- PRE/EVENT kind confirmed: <kind>, done at <UTC timestamp>
```

## Keeping the codebase understandable as it grows

- Each module's `README.md` is a **one-page card** (≤ 80 lines): purpose, tables, public functions (signatures), events published and consumed, permissions, executors, jobs. An agent working in another module reads the card instead of the code.
- `public.py` is small and stable. Adding to it is fine. Changing or removing anything in it requires updating every caller in the same task, and the task must say so.
- No "utils" dumping grounds. Shared code goes to `app/platform` only through a Tier-A task.
- When a file approaches 400 lines, split it in the same task.
- Search before you write: if a helper exists in `platform/` or your module, use it.

## Proposed new `AGENTS.md` (owner adopts in task P0-04)

The current `AGENTS.md` allows only documentation and synthetic data. Building the PRE tasks needs the owner to replace it with something like the following. The owner decides the final text.

```markdown
# Rules for coding agents

## Source of truth
docs/roadmap/ is the single source of truth. Work one task at a time from docs/roadmap/phases/STATUS.md
following docs/roadmap/12-agent-playbook.md. If code and roadmap disagree, stop and ask.

## Event boundary (Vultr Agent Rush, kickoff 2026-11-03 15:00 UTC)
- Before kickoff: only tasks of kind PRE (non-AI platform, ERP modules, UI, packaging, synthetic data).
  Do not implement inference calls, prompts, agent loops, AI tools, evals or voice before kickoff.
- Record every PRE task in PRE_EVENT_WORK.md with the UTC date. Never alter timestamps or rewrite history.

## Always
- Synthetic data only (example.com emails, invented names). No real customer data.
- Never commit secrets (.env, keys, Terraform state). Never print secrets in logs or reports.
- Run `make check` before reporting a task done. Report tests honestly, including ones not run.

## Never without the owner's explicit approval in chat
- Creating remotes, GitHub resources, pushing, or publishing anything.
- Creating or changing cloud resources, spending money, redeeming credits, using real API keys.
- Installing system-wide software (project-local dependencies via uv/pnpm lockfiles are allowed once P1-01 is approved).
- Adding a new dependency, service or container not covered by docs/roadmap/03-decisions.md.

## Commits
Local commits are allowed with Conventional Commit messages including the task ID. Do not amend or rebase commits
that the owner has seen. Preserve other projects on this machine.
```
