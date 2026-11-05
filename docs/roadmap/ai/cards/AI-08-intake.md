# AI-08 · U1 Intake, part 1: trigger, safety, customer matching, guards, notes

Kind **EVENT** · Tier A · Size M · Depends on: AI-05, PR-02 · Blocks: AI-08B

## Goal
Everything around the intake agent that is decided **in code**: react once to each inbound customer message, screen
public chat input, match the customer by deterministic rules, avoid duplicate work, and leave a clear note for staff.
At the end of this card intake runs end to end with a *placeholder* agent step (`intake.run_agent_step`, which part 2
replaces) so every path is tested without the real prompt.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/architecture.md` §4 (contexts), §5 (U1), §7.
2. `docs/roadmap/ai/contracts.md` §8 (`RunResult`, status table, `record_early_result`, `background_ctx_factory`),
   §9 (`complete`), §11 (`intake`, `safety`, `SafetyVerdict`), §13 (subscriber and job rows; non-transactional jobs).
3. `docs/roadmap/ai/integration-map.md` §A (registration), §B "Inputs and guards" and the `find_customers` row,
   §E (`conversations.message_received`), §F (internal note, handoff), §G (fixtures).
4. `docs/roadmap/ai/testing.md` §1 (`fake_router_by_class` to script the `safety` and `agent` classes separately).
5. `backend/app/modules/ai/` — `HANDOFF.md` notes, `recording.py`, `structured.py`, `agent/contexts.py` (signatures only).

## You build
- `ai/subscribers.py`: on `conversations.message_received` with `author_kind in {"visitor","customer"}` →
  `jobs.enqueue(db, "ai.intake", {"message_id": …}, business_id=ctx.business_id, dedupe_key=f"intake:{message_id}")`.
  Nothing for staff, assistant or system messages. Register in `registration.py`.
- `ai/jobs.py`: `ai.intake` registered **non-transactional**: `handler(ctx, payload)` → `usecases.intake.run(...)`.
- `ai/usecases/safety.py`: `screen(ctx_factory, text, trigger_key) -> SafetyVerdict | None` via `structured.complete`
  (class `safety`, prompt `safety`); returns `None` when the `safety` class has no allowed route. Write the prompt
  `ai/prompts/safety.v1.md` (classify the fenced text into `SafetyVerdict`; flag abuse, self-harm, sexual or violent
  content; ordinary complaints and angry customers are **not** flagged; output `SafetyVerdict`).
- `ai/usecases/intake.py`: `run(message_id: UUID, business_id: UUID, *, router: Router | None = None) -> RunResult`:
  1. `trigger_key = f"intake:{message_id}"`; load `conversations.get_message_context` and `org.get_business_brief`
     (system context with `conversations.thread.read`, `org.business.read`); visitor contact fields go into
     `server_facts` (passed to part 2 for `RunSpec.server_facts`) only.
  2. **Duplicate guard:** `approvals.find_open_proposals(conversation_id=…, action_types=["scheduling.book_appointment",
     "conversations.send_reply"])` non-empty → `record_early_result(needs_human=True, result={"reason": "open_proposal"})`.
  3. **Safety** (channel `web_chat` only): flagged → `conversations.request_handoff(reason="Message needs a person")`,
     early result `{"reason": "safety"}`; `None` **or any `AiError` from the screen** → continue with
     `extra={"safety_unchecked": True}` (the agent run will meet the same budget/provider problem and record it).
  4. **Customer matching (frozen; changed 2026-11-04, owner-level decision by supervising tech lead):** linked
     conversation → that customer. Otherwise exact normalized e-mail/phone matches take precedence: if a contact was
     supplied, keep only CRM candidates with reason `email` or `phone`; no contact match → new regardless of name.
     Without contact, exclude candidates unless `crm.has_identity_name_overlap` finds at least two distinct shared
     name tokens (ignoring case, whitespace and punctuation). A surname or any other single token is insufficient.
     Apply the existing score rule to retained candidates: best ≥ 0.8 **and** (no second or best − second ≥ 0.1) →
     matched; no score ≥ 0.5 → new; otherwise ambiguous. Ambiguous visitors still run the agent with **customer not
     identified**, no matched id/summary; retain `{"reason": "ambiguous_customer", "candidates": [up to 3 display names]}`
     in result extras and append the candidate-linking note to the normal outcome in step 6. Stock/service replies
     remain possible; existing-customer booking requires a hold for an identified customer. See contracts §10.
  5. Call `run_agent_step(ctx_factory, message_ctx, match, state)` — in this card a placeholder that returns a
     `RunResult` with `needs_human=True` and `extra={"placeholder": True}`; AI-08B replaces its body.
  6. **Outcome note** — exactly one internal note per message (`author_kind="assistant"`), text by this table
     (frozen), ending with the marker `[intake:<message_id>]`; on retry, skip if a note with that marker exists:

     | Result | Note text |
     |---|---|
     | proposals created | "Prepared for approval: <titles from `RunResult.extra["proposal_titles"]`, joined by "; ">." |
     | `needs_human`, reason `open_proposal` | "A proposal for this conversation is already waiting in Approvals." |
     | `needs_human`, reason `safety` | "This message was passed to a person without AI handling." |
     | reason `ambiguous_customer` (append to any outcome above/below) | "Several customers could match (<names>). Please link the right one." |
     | `needs_human` from the agent's final step | "Needs a person: <final.message_to_staff>" |
     | `policy_blocked` | "AI is turned off for this business, so no proposal was prepared." |
     | `budget_exceeded` | "Today's AI budget is used up, so no proposal was prepared." |
     | `failed`, `step_limit` (any error code) | "Could not prepare a proposal; please handle this message." |

## Tests (write first)
`test_intake_enqueued_once_per_inbound_message`, `test_intake_ignores_staff_assistant_system_messages`,
`test_job_runs_without_open_transaction`, `test_open_proposal_skips_with_early_result`,
`test_flagged_public_message_hands_off_without_agent_call`, `test_safety_unavailable_marks_unchecked_and_continues`,
`test_customer_matching_rule_matched_new_ambiguous`, `test_ambiguous_customer_note_lists_names_only`,
`test_one_outcome_note_per_message_even_on_retry`, `test_visitor_contacts_not_in_any_model_request`.

Regression coverage (changed 2026-11-04): Bea Example with a new e-mail and a shared surname runs the agent as
new; exact normalized e-mail/phone matches win; contact-free exact full names match; surname-only or first-name-only
overlap does not count; ambiguous identities can prepare a stock reply while retaining the candidate note. Add
`new-shared-surname-stock.yaml` and `new-shared-surname-booking.yaml` to the intake eval cases. This dated card entry
records the EVENT correction within the task's restricted edit scope.

## Done when
`make check` passes; `HANDOFF.md` rewritten (the placeholder and what AI-08B must replace).
