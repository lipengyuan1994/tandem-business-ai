# Event schedule (3 Nov 15:00 UTC → 8 Nov 22:00 UTC)

Written 2026-11-03 (DOC, before kickoff). It orders the tasks in [P5](P5-ai-layer.md), [P6](P6-event-hardening.md) and [P7](P7-submission.md) so that no event time is spent planning. The task files stay the source of the steps and acceptance checks; this page decides only **order, parallel tracks, time boxes and cuts**. Times are UTC. Re-check the submission deadline on the live event page on day 1.

## Before kickoff: owner checklist

Each item is the owner's decision or action. None of them may be done by an agent without the owner's approval in chat.

| Item | Why it blocks | Fallback if not done |
|---|---|---|
| Local git commits allowed (Q5) | The pre-event / event split is easiest to prove with dated commits; P7-01 needs a history to publish | `PRE_EVENT_WORK.md` log only (weaker evidence) |
| Vultr account ready, payment method on file, how the event credit is redeemed known | P4-09 (first deploy) and the Serverless Inference key gate P5-01 | Build P5 against recorded fixtures; deploy on day 2 |
| Domain for the demo (Q3) | HTTPS certificate for the public URL | `<ip>.sslip.io` (Let's Encrypt rate limits may apply) |
| Solo or team, who approves Tier-A work (Q2) | Approvals in the demo need two people; reviews need a second reader | Second model review (Codex Sol) + owner |
| External fallback providers allowed (Q9) | If no Vultr model meets the eval thresholds | Vultr only; document the gap |

## Critical path (card IDs from the [AI build kit](../ai/README.md))

```
P4-09 deploy + key ──────────────────────► AI-04 probe ─────────► AI-09 evals + model choice ─┐
AI-01 ─► AI-02 ─► AI-03 ─► AI-05 ─┬─► AI-08 ─► AI-08B ─► AI-14 ─────────────────────────┤
   │                              ├─► AI-10 copilot                                           ├─► P6-05 redeploy ─► P6-04 rehearsals ─► P7
   ├─► AI-06 ─► AI-07 ────────────┼─► AI-11 summary / reorder / briefing ─► AI-15             │
   │                              └─► AI-12 procurement (needs PQ-01)                         │
   └─► AI-13 frontend (mocks first, integrate as backends land) ──────────────────────────────┘
```

Intake (AI-08, AI-08B) is the demo's backbone; procurement (AI-12) is the second showcase (challenge focus area
"Procurement"). Everything else is behind them.

## Day by day (three agents in parallel, each in its own worktree and test database, card PR-00)

| When (UTC) | Track 1 (Tier A: engine) | Track 2 (Tier A: tools, procurement) | Track 3 (Tier B: frontend) + owner | Exit check |
|---|---|---|---|---|
| **Tue 3 Nov 15:00–23:00** | Record start in `PRE_EVENT_WORK.md`. AI-01 → AI-02 | waits for AI-01, then AI-06 | AI-13 against mocks. Owner: P4-09 deploy and inference key | AI-01, AI-02 DONE; demo URL serves the non-AI app |
| **Wed 4 Nov** | AI-03 → AI-04 (key) → AI-05 | AI-06 → AI-07 | AI-13 panel, banners, settings (mocks) | Probe report for ≥ 4 Vultr models; runner tests green |
| **Thu 5 Nov** | AI-08 → AI-08B intake on the demo VM | AI-12 procurement | AI-13 integrate intake/approvals pieces; AI-11 (Tier B) | Demo chat message → booking proposal ≤ 20 s |
| **Thu 5 Nov 18:00** | **Decision point:** if AI-08B is not green, cut AI-10 copilot, AI-15 and voice now | — | — | Written in `PRE_EVENT_WORK.md` |
| **Fri 6 Nov** | AI-09 evals + route choice; AI-14 safety | AI-10 copilot | AI-13 integrate copilot, summary, procurement | `make eval` thresholds met or gap documented; injection 100 % |
| **Sat 7 Nov** | P6-05 redeploy, P6-06 scan, P6-07 performance, P6-08 restore | AI-15 (campaign draft; U10 only if ahead) | P6-04 rehearsal 1 | Smoke and e2e pass on the public URL |
| **Sun 8 Nov until 18:00** | P6-04 rehearsal 2; freeze (P7-06) | — | P7-02 README final, P7-04 slides | Two clean rehearsals |
| **Sun 8 Nov 18:00–22:00** | Owner: P7-01 publish repo, P7-03 video, P7-05 submit | — | — | Submitted (aim 20:00) |

## Cut list (in this order)

1. P6-03 voice.
2. P5-02 local CPU benchmark (keep the written feasibility estimate in [08](../08-ai-layer.md)).
3. AI-15 U10 payment follow-up, then U5 campaign draft.
4. AI-10 copilot chat (intake, procurement and the one-click features still show the agent).
5. AI-11 briefing and reorder note (keep summary and suggested reply).
6. AI-12 flows 4 (chase) then 2 (pasted replies; the supplier form still works without AI).

Never cut: approvals for every write, the eval report, the injection suite, the pre-event disclosure, honest
limitations in the README.

## ERP work built before kickoff that the agent may use

Agent tools (P5-05) call only `public.py` functions. Modules finished before kickoff (for example sales quotes or purchase orders, if built) get tools only when their executor and approval form already exist. Adding a tool is event work and is recorded as such.
