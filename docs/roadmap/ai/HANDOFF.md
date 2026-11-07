# AI build handoff (current state)

Keep this current and under 80 lines. History and detailed evidence live in EVENT_WORK.md and the cards.

## State (2026-11-07 UTC)

- Owner reports P10 released in **0.1.9**, consistent with the recorded release entry in EVENT_WORK.md.
  This task has not contacted the demo VM, a provider, SMTP or a browser stack.
- P10 covers autonomy/Work, delayed delivery/Stop, hand-over/grounded answers, local-only regulated routing,
  dictation, Team/chains, Customer Care, dental, knowledge/PDF, embed and customer self-service.
- Contracts: [contracts.md](contracts.md); task tracking: [STATUS](../phases/STATUS.md).
- Latest recorded release checks and live evals are in EVENT_WORK.md; those observations are not rerun here.
  Clinic data is synthetic; production regulated routing requires local models under clinic control.
- Demo deployment record: Vultr smb-demo, https://tandemai.open-dl.com (until 2026-11-07 https://45-63-85-83.sslip.io), version 0.1.11.
  Resource accounting: [vultr-spend.md](../../vultr-spend.md). No cloud resources changed in P10-gaps.

## P10-gaps (Codex, 2026-11-07 UTC)

Implementation and reviewer-run browser specs complete. Full backend once: 2,397 pass / one permission-
inventory omission, corrected; 30 focused pass afterward. Full frontend: 539 pass. Scoped make check passes
with test targets excluded; full backend was not rerun. Full evidence/limitations are in the gap card.
Owner explicitly prohibits installs, downloads, external calls, Docker and commits in this task.

- Contracts updated first. Work now has emergency, priority Care and ordinary chronological bands.
  Care cursors include priority/timestamp/UUID; boundary lookahead never consumes an unreturned row.
  Unchanged rows paginate without duplicates/skips; this remains a live keyset view, not a snapshot.
- Care Work metadata: category, version and display-only customer_name, null without crm.customer.read.
  Frontend preserves server order and uses supplied names/category; version refreshes expanded case state.
- Chain depth measures recorded run/proposal depth, default 0 without such records. Events/jobs and
  proposal-derived evidence also expose their known depth. Admission depth/caps are unchanged.
- AgentOut exposes registry triggers and switchable. All current enabled PATCHes are supported.
  UI uses plain trigger chips and capability-based switches; legacy Coming-soon handling removed.
- GET /ai/agents/addressable uses ai.assistant.use, returns only key/name/enabled and Manager enabled/reason.
  Assistant @ and Manager-off no longer require proposal-read. Existing endpoint permissions unchanged.
- E2E specs: self-service email-link/fresh-context cancellation with acknowledgment/staff notification;
  actual localhost:5555 embed, sessionStorage resume, disallowed-origin CSP refusal; deterministic dental
  emergency reply and human hand-over. Ellery's login is created through dev-account like Casey's.
  These specs have NOT been run; reviewer must use the reseeded Docker dev stack.
- No migration. Focused backend checks use only smb_gaps, observed at 20261106_1500_self_service (head).
  Native arm64 uv/Python/Node and compiled DB/frontend bindings verified; no new runtime selected.
- Existing node_modules .bin shims pointed at a removed smb-ops-b2ui worktree. Their local paths were
  repaired without installs. pnpm runs offline with automatic dependency verification disabled.
- Evidence, files, decisions, exact commands and outstanding validation: [P10-gaps](cards/P10-gaps.md).

Reviewer widget follow-up (2026-11-07): reviewer reports original Chromium send/reload/CSP checks work.
Loader v1.0.1 now gates closed-root mounting on valid config and reserves header space via flex layout;
heading focus prevents scroll. 60 widget / 554 frontend tests, types/lint/build and frontend-scoped make check
pass; loader 1,714 B gzip, initial widget JS 76,875 B gzip. Updated 1280/375 px e2e remains reviewer-run.

## Prior implementation evidence

- [P10-B1 backend](cards/P10-B1-backend.md), [B3 screens](cards/P10-B3-screens.md),
  [B2 backend](cards/P10-B2-backend.md), [B2 screens](cards/P10-B2-screens.md).
- [E0 routing](cards/P10-E0-routing.md), [local benchmark](../../../backend/evals/reports/2026-11-06-local-models.md),
  [voice backend](cards/P10-D-backend.md), [voice screens](cards/P10-D-screens.md).
- [E1 dental](cards/P10-E1.md), [C2a knowledge/PDF](cards/P10-C2a.md), [C2b embed/self-service](cards/P10-C2b.md).
  Earlier card-local disk-full/check limitations are historical; read the later release work record.
- Security: [AI review](../../security/2026-11-03-ai-second-review.md),
  [ops readiness](../../security/2026-11-03-ops-readiness-review.md), [event pass](../../security/2026-11-event-review.md).

## Owner/open operational items

Customer domain, alert destination/external uptime check, real SMTP, second backup copy, named operators,
and relevant agreements remain separate operational work. The recorded demo access procedure is
`deploy/scripts/access.sh`; no access or deployment is authorized by this gap task.
