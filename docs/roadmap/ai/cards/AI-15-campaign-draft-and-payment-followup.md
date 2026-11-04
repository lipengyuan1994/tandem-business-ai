# AI-15 · U5 campaign draft; U10 overdue payment follow-up (optional)

Kind **EVENT** · Tier B · Size S · Depends on: AI-11 (single-shot pattern), PR-01 (overdue invoices, reminder executor) ·
U10 is **optional**: do it only if the event schedule is ahead (cut list in `docs/roadmap/phases/P5-event-schedule.md`).

## Goal
U5: draft a marketing campaign's subject and body from its purpose and segment, as a new revision a person edits and
submits (consent filtering stays deterministic in marketing). U10 (finance focus area): for each overdue invoice, draft a
polite reminder as a proposal a person approves.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/contracts.md` §9, §11 (`campaign_draft`, `payment_followup`, `CampaignDraft`, `PaymentReminderDraft`),
   §13 (payment cron), §14 (`/campaigns/{id}/draft`).
2. `docs/roadmap/ai/integration-map.md` §B (`list_overdue_invoices`), §D (`marketing.add_revision`,
   `propose_payment_reminder`).
3. `backend/app/modules/ai/usecases/summary.py` (your template from AI-11).
4. `docs/roadmap/modules/M12-marketing.md` "As built" (revisions, what a draft may contain).

## You build
- `ai/usecases/campaign_draft.py` + `POST /ai/campaigns/{id}/draft` (context `staff_ctx_factory(..., "campaign_draft")`,
  which adds the marketing permissions of contracts §3 when the user has them): facts = business brief, campaign purpose, segment
  description (no customer data) → `CampaignDraft` (writer, prompt `campaign_draft`) → `marketing.add_revision(...,
  created_by_kind="assistant", ai_run_id=run_id)`. The `marketing_campaign` template already appends the sender line and
  the unsubscribe link (`notifications/templates.py`), so code **rejects drafts that contain any URL or the word
  "unsubscribe"** (one repair attempt).
- `ai/usecases/payment_followup.py` + per-business cron `ai-payment-followup-scan` (daily, non-transactional job): for each overdue invoice with an e-mail and
  no reminder in the last 7 days and no pending reminder proposal → `PaymentReminderDraft` (writer, prompt
  `payment_reminder`) → `propose_payment_reminder`. Amounts and dates in the text must match the invoice (code check).
- Prompts `campaign_draft.v1.md`, `payment_reminder.v1.md`: fencing sentence; no invented offers, prices, dates; short,
  friendly, in the business's voice.

## Tests (write first)
`test_campaign_draft_adds_assistant_revision`, `test_campaign_draft_rejects_urls_and_own_unsubscribe_text`, `test_campaign_draft_uses_no_customer_data`,
`test_payment_followup_one_proposal_per_overdue_invoice_with_cooldown`, `test_payment_followup_rejects_wrong_amounts`,
`test_payment_followup_skips_without_email`.

## Done when
`make check` passes; frontend "Draft with AI" button on the campaign page (P6-02) wired to the endpoint with a test;
`HANDOFF.md` rewritten.
