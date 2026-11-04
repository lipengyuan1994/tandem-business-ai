# P7 · Submission (Nov 7–8)

**Goal:** a complete, honest submission before **8 Nov 2026 22:00 UTC** (re-check the deadline on the live page). Every publishing step needs the owner's explicit approval.

### P7-01 · Publish the repository
Kind: GATE · Owner · Size: S · Depends on: P6 gate
Steps: the owner approves creating a public GitHub repository (MIT). Before pushing: run `gitleaks` on the full history, make sure no `.env` or Terraform state is tracked, and that `PRE_EVENT_WORK.md` is complete. History is pushed as-is (no rewriting).
Done when:
- [ ] Public repo URL recorded.

### P7-02 · Judge-facing README
Kind: DOC · Tier: B · Size: M · Depends on: P6-04
Content: what it is (one paragraph); a 60-second demo GIF or video link; architecture diagram ([02](../02-architecture.md)); **how Vultr is used** (Compute VM, Serverless Inference with model IDs, Object Storage, Container Registry, Terraform provider); AI design (proposal-only agent, approvals, evals with scores); **pre-event work disclosure** (link to `PRE_EVENT_WORK.md`, what was PRE and what was EVENT); setup instructions (`install.sh`, `make dev`); limitations and next steps.
Done when:
- [ ] A different model, given only the README, can explain the architecture and the AI/pre-event split correctly.

### P7-03 · Demo video
Kind: GATE · Owner · Size: M · Depends on: P6-04
Steps: record the demo script (about 3–5 minutes) on the public URL, including the failure moments and the Vultr console view of the running VM and inference usage.

### P7-04 · Slides
Kind: DOC · Tier: B draft, owner final · Size: S
Outline: problem → product → live flow → architecture on Vultr → safety and approvals → evals and cost → business model (customer-owned installs) → roadmap.

### P7-05 · Submit on lablab.ai
Kind: GATE · Owner · Size: S · Depends on: P7-01 … P7-04
Done when:
- [ ] Submission confirmed with title, descriptions, tags, cover image, video, slides, repo URL, demo URL and application URL.

### P7-06 · Freeze
Kind: PRE · Tier: B · Size: S
Steps: tag `v0.1.0`; keep the demo running through judging; record spend; note the demo teardown date decided by the owner.

## Exit gate P7
- [ ] Submission confirmed before the deadline; demo URL healthy.
