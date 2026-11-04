# P0 · Foundation (Nov 3)

**Goal:** the owner approves the plan and unblocks the build, and the event organisers are asked about prior work. No code is written in P0.

### P0-01 · Review the roadmap and answer open questions
Kind: GATE · Owner · Size: M · Depends on: —
Read first: [README](../README.md), [01](../01-product-and-scope.md), [02](../02-architecture.md), [03](../03-decisions.md), [13](../13-risks-and-open-questions.md)
Steps:
1. Read the documents above. Mark any ADR you disagree with as `PROPOSED CHANGE` with a note.
2. Answer Q2, Q3, Q5 and Q6 in [13](../13-risks-and-open-questions.md#open-questions) (team and reviewer, domain, commits, demo vertical).

Done when:
- [ ] Every ADR is either accepted or has a written change request.
- [ ] Q2, Q3, Q5 and Q6 have answers.

### P0-02 · Ask the organisers about the pre-built non-AI base
Kind: GATE · Owner · Size: S · Depends on: —
Steps:
1. Send a message through an official channel (lablab Discord or event support). **The owner sends it; agents do not.** Suggested text:

   > Hello! I'm preparing for the Vultr Agent Rush (Challenge 2). Before kickoff I plan to build non-AI groundwork for my project: a small ERP (customers, scheduling, inventory, approval queue, deployment scripts) with no AI functionality. All AI features (model integration, agent, prompts, evals) would be built during the event, and everything built before kickoff would be disclosed with dates in the repository. Is that acceptable under the rules, or should the pre-built part be limited further?

2. Record the date sent and the answer in `PRE_EVENT_WORK.md` and update Q1 / ADR-014.

Done when:
- [ ] The question was sent (date recorded). If the answer restricts PRE work, the owner updates ADR-014 and the phase plan before P1 continues.

### P0-03 · Approve the local toolchain
Kind: GATE · Owner · Size: S · Depends on: —
Steps: approve (or change) installing: Docker Desktop **or** OrbStack; `uv`; Node.js 24 LTS + `pnpm` (via Corepack); Terraform ≥ 1.9; optional `gitleaks`, `trivy`, `k6`. Record the decision in Q4.

Done when:
- [ ] Q4 answered. Nothing is installed by an agent before this.

### P0-04 · Adopt the new AGENTS.md
Kind: GATE · Owner · Size: S · Depends on: P0-01
Steps: review the proposed text in [12-agent-playbook.md](../12-agent-playbook.md#proposed-new-agentsmd-owner-adopts-in-task-p0-04), edit as desired, and replace `/AGENTS.md`. Record the change in `PRE_EVENT_WORK.md`.

Done when:
- [ ] `/AGENTS.md` permits PRE tasks. Until then, agents may only do DOC tasks.

### P0-05 · Commit policy and first commit
Kind: GATE · Owner · Size: S · Depends on: P0-04
Steps: if Q5 = yes, the owner (or an agent on the owner's instruction) makes the first local commit of the current preparation files, unchanged, so the history starts from an honest snapshot. No remote.

Done when:
- [ ] `git log` shows the initial commit, or the decision "no commits yet" is recorded.

### P0-06 · Synthetic dataset matches the roadmap
Kind: DOC · Tier: B · Size: S · Depends on: —
Read first: [05](../05-data-model.md), `data/README.md`
Steps: `data/fixtures.json` and `data/schema.sql` cover the MVP demo story (two businesses in different time zones, all roles, mixed consent, services with parts, resources, a conflicting slot, low stock, a pending proposal).

Done when:
- [ ] `python3 -I data/validate_fixtures.py` prints `OK`. *(Completed 2026-11-03.)*

## Exit gate P0
- [ ] P0-01 … P0-05 done; P0-02 at least sent.
- [ ] The owner says "start P1" in chat.
