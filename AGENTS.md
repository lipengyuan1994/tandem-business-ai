# Rules for coding agents

Adopted 2026-11-03 by the owner (replaces the documentation-only preparation boundary; see PRE_EVENT_WORK.md).

## Source of truth
docs/roadmap/ is the single source of truth. Work one task at a time from docs/roadmap/phases/STATUS.md
following docs/roadmap/12-agent-playbook.md. If code and roadmap disagree, stop and ask.

## Event boundary (Vultr Agent Rush, kickoff 2026-11-03 15:00 UTC)
- Before kickoff: only tasks of kind PRE or DOC (non-AI platform, ERP modules, UI, packaging, synthetic data).
  Do not implement inference calls, prompts, agent loops, AI tools, evals or voice before kickoff (tasks of kind EVENT).
- Record work in PRE_EVENT_WORK.md / EVENT_WORK.md with the date. Never rewrite git history (no amend, rebase or
  force-push of commits the owner has seen).

## Always
- Synthetic data only (example.com emails, invented names). No real customer data.
- Never commit secrets (.env, keys, Terraform state). Never print secrets in logs or reports.
- Run `make check` before reporting a task done. Report tests honestly, including ones not run.

## Never without the owner's explicit approval in chat
- **Git commits** (each one needs the owner's approval; the first commit was approved 2026-11-03), creating remotes,
  GitHub resources, pushing, or publishing anything.
- Creating or changing cloud resources, spending money, redeeming credits, using real API keys. (2026-11-03: the owner
  allowed test deploys on Vultr within the $200 credit for the whole project; log every resource in docs/vultr-spend.md.)
- Adding a new dependency, service or container not covered by docs/roadmap/03-decisions.md.

## Allowed
- Installing local developer tools needed by the roadmap (owner approval given 2026-11-03), and project-local dependencies through the uv/pnpm lockfiles.
- Preserve other projects on this machine. Mind free disk space; prune only this project's Docker resources.
