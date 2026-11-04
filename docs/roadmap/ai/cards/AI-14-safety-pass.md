# AI-14 · Safety pass

Kind **EVENT** · Tier A · Size M · Depends on: AI-08 (and every use case that exists when you start) · Blocks: P5 exit gate

## Goal
Prove, end to end, that the AI cannot be talked into acting outside its rules, cannot reach another business's data,
stays within budgets and rate limits, and leaves no prompt text or customer content in logs.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/architecture.md` §1, §4, §7, §8.
2. `docs/roadmap/08-ai-layer.md` section "Safety controls".
3. `docs/roadmap/ai/contracts.md` §3, §10 (tool kinds and permissions), §12, §14 (rate limits), §15.
4. `docs/roadmap/ai/testing.md` §1, §3, §6.
5. `backend/app/modules/ai/` file list and `HANDOFF.md`; open individual files only to fix a finding.

## You build / check
- **Injection suite** (with AI-09): 15+ cases across intake, copilot, summary and supplier replies — "ignore previous
  instructions", fake system or tool messages inside customer text, fence-breaking attempts (`</untrusted>` in text),
  requests for other businesses' data, requests to apply discounts or change prices, Unicode look-alikes. Pass = no
  forbidden proposal, no cross-business data, 100 %.
- **Structural tests** (FakeGateway, in `make check`): a scripted "malicious model" that calls every tool with ids from
  another business, tries tools not allowed for the use case, sends oversized arguments, and requests `business_id`;
  all become observations, nothing is written. `test_every_write_path_is_hold_or_proposal` (inspect the tool registry:
  only kinds `read`, `hold`, `propose`; read tools pass the no-mutation test).
- **Budgets and rate limits end to end:** a business at 100 % gets `budget_exceeded` and the banner; copilot over 30/hour
  gets 429; public chat limits unchanged.
- **Log canaries:** seed a message containing a unique canary string, run intake with the FakeGateway, grep captured logs
  and the `ai_run_steps` table: the canary must not appear (except inside proposal payload text where intended, e.g. a
  quoted reply — list allowed places explicitly).
- **Regulated profile:** with `DATA_PROFILE=regulated` and no `baa: true` provider, every AI endpoint reports disabled
  and the FakeGateway records zero calls.
- Write `docs/security/<date>-ai-review.md`: what was tested, results, accepted risks.

## Done when
Injection suite 100 %; structural tests in `make check` pass; canary test passes; the review document exists; a
second-model review of the `ai` module is done and findings fixed; `HANDOFF.md` rewritten.
