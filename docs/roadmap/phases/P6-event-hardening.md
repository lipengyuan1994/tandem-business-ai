# P6 · Event hardening (Nov 5–7)

**Goal:** a demo that works every time on the public URL, plus the marketing slice and (only if time allows) voice.

### P6-01 · Marketing backend
Kind: PRE-type work (non-AI); may start before kickoff if P3 finished early · Tier: B · Module: M12 · Size: L · Depends on: P3-02, P2-07
Read first: [M12](../modules/M12-marketing.md)
Done when:
- [ ] All M12 tests pass, including consent exclusion and the suppression recheck at execution.

### P6-02 · Marketing frontend and AI draft
Kind: EVENT (AI draft) · Tier: B · Size: M · Depends on: P6-01, P5-08
Done when:
- [ ] Segment builder, audience preview with exclusion reasons, revisions, the "Draft with AI" button (creates a revision through `/ai/campaigns/{id}/draft`) and submit for approval; component tests pass.

### P6-03 · Voice (stretch; first item on the cut list)
Kind: EVENT · Tier: A · Module: M14 · Size: L · Depends on: P5-06
Start only if the P5 exit gate passed by Nov 5 18:00 UTC.
Done when:
- [ ] M14 tests pass; push-to-talk in Inbox and the assistant; denial falls back to text.

### P6-04 · Demo data and demo script
Kind: DOC + PRE · Tier: B · Size: M · Depends on: P5-06
Create: `docs/demo-script.md` (5-minute flow following [01](../01-product-and-scope.md#the-flagship-workflow-demo-script-backbone), with the failure moments), `python -m app.cli reset-demo` (re-seeds the demo businesses only).
Done when:
- [ ] Two consecutive rehearsals on the demo URL follow the script without manual fixes.

### P6-05 · Deploy and verify on Vultr
Kind: DEPLOY · Tier: A · Size: S · Depends on: P5 gate
Done when:
- [ ] `upgrade.sh 0.1.x` succeeds on the demo VM; `smoke.sh` and `make e2e BASE_URL=https://<demo>` pass.

### P6-06 · Security pass
Kind: PRE/EVENT · Tier: A · Size: M · Depends on: P6-05
Steps: ZAP baseline against the demo URL; `pip-audit`, `pnpm audit`; external port scan (v4 and v6); egress test (from the api container, `curl https://example.org` must fail through `platform.http`); review of headers and cookies.
Done when:
- [ ] No high findings, or each is fixed or accepted in writing in `docs/security/2026-11-event-review.md`.

### P6-07 · Performance sanity
Kind: PRE · Tier: B · Size: S · Depends on: P6-05
Done when:
- [ ] 20 concurrent simulated staff on the demo VM for 5 minutes: p95 read < 300 ms, no errors; results recorded.

### P6-08 · Restore drill on the demo
Kind: DEPLOY · Tier: A · Size: S · Depends on: P6-05
Done when:
- [ ] Latest Object Storage backup restored into a scratch DB on the VM; row counts match; duration recorded (recovery-time measurement).

### P6-09 · Minimal System page
Kind: PRE · Tier: B · Module: M18 (subset) · Size: S · Depends on: P4-03
Done when:
- [ ] Version, queue age, dead jobs, outbox issues, last backup age, AI usage today.

## Exit gate P6
- [ ] Demo script passes twice on the public URL; security review written; restore drill recorded; spend recorded.
