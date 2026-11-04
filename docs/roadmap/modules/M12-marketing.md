# M12 · marketing

| Field | Value |
|---|---|
| Release / phase | MVP (segments, audience preview, drafts, simulated send) · P6 |
| Tier / kind | B · PRE for segments, audience and campaigns; EVENT for AI drafting (done in M13, U5) |
| Depends on | platform, org, crm, catalog, scheduling, approvals, notifications |
| Owns tables | `segments`, `campaigns`, `campaign_revisions`, `audience_snapshots`, `audience_members`, `suppressions` |

## Purpose
Consent-aware campaigns. Staff define who (segment), what (content revision) and which channel. The backend filters consent and suppression **deterministically**, freezes an audience snapshot at approval and delivers through the outbox (simulated in MVP). The AI only drafts content.

## Tables
`segments` — `name`, `definition jsonb` validated by `SegmentDefinition` (a whitelisted filter list, no free SQL):
`{"all": [{"field": "tag", "op": "has", "value": "road-bike"}, {"field": "last_interaction_at", "op": "older_than_days", "value": 180}, {"field": "has_appointment_for_service", "op": "eq", "value": "<service_id>"}]}`.
Allowed fields: `tag`, `last_interaction_at`, `created_at`, `has_appointment_for_service`, `customer_kind`.

`campaigns` — `name`, `purpose` (text), `channel` (`email`), `segment_id`, `status` (`draft` \| `in_review` \| `approved` \| `sending` \| `sent` \| `cancelled`), `current_revision_id`, `approved_snapshot_id`, `scheduled_for` (v1).

`campaign_revisions` — immutable: `campaign_id`, `revision_no`, `subject`, `body_markdown`, `created_by_kind` (`staff` \| `assistant`), `created_by`, `ai_run_id`.

`audience_snapshots` — `campaign_id`, `segment_definition` (copy), `computed_at`, `eligible_count`, `excluded_counts jsonb` (`{no_consent: n, suppressed: n, no_contact: n, archived: n}`), `hash`.
`audience_members` — `snapshot_id`, `customer_id`, `contact_point_id`.

`suppressions` — `channel`, `value_normalized` (**PII**), `reason` (`unsubscribed` \| `bounced` \| `complaint` \| `manual`), `created_at`. Unique `(business_id, channel, value_normalized)`.

## Business rules
1. Eligibility = segment match ∧ active customer ∧ contact point for the channel ∧ **current marketing consent `granted`** for that channel (from `crm.list_marketing_eligible`) ∧ not suppressed.
2. Preview shows the eligible count, excluded counts by reason and a sample of 10. Staff can page through the full list.
3. Submitting for review creates a `marketing.approve_campaign` proposal binding `revision_id` and a freshly computed snapshot hash. If content or snapshot changes, re-approval is required.
4. Execution re-checks suppressions **immediately before enqueueing** each message. Customers newly suppressed since the snapshot are skipped and counted.
5. Every message includes an unsubscribe link (`/api/v1/public/{slug}/unsubscribe?token=…`, signed, single-purpose). Unsubscribing appends a `withdrawn` consent record in crm and adds a suppression.
6. MVP channel: email through the **simulated** adapter only. Real sending is a v1 feature gated on SMTP configuration and owner approval.

## Public interface (`public.py`)
```python
def preview_audience(db, ctx, segment_id) -> AudiencePreview
def get_campaign(db, ctx, campaign_id) -> CampaignDto
def add_revision(db, ctx, campaign_id, subject, body_markdown, *, created_by_kind, ai_run_id=None) -> RevisionDto   # ai U5 uses this
```

## API
| Method | Path | Permission |
|---|---|---|
| GET / POST | `/marketing/segments` | `marketing.campaign.read` / `.write` |
| GET | `/marketing/segments/{id}/preview` | read |
| GET / POST | `/marketing/campaigns` | read / write |
| GET / PATCH | `/marketing/campaigns/{id}` | read / write |
| POST | `/marketing/campaigns/{id}/revisions` | write |
| POST | `/marketing/campaigns/{id}/submit` | write (creates the proposal) |
| GET / POST | `/marketing/suppressions` | read / write |
| GET | `/public/{slug}/unsubscribe` | public (signed token) |

## Permissions
`marketing.campaign.read`, `marketing.campaign.write`, `marketing.proposal.approve`.

## Executors
`marketing.approve_campaign` (payload: `campaign_id`, `revision_id`, `snapshot_hash`). Run: set status `sending`, enqueue one outbox message per member (idempotency key `campaign:{id}:{customer}`), then `sent`. `describe()` shows channel, eligible count, exclusions by reason, subject and the first 300 characters.

## Error codes
`marketing.invalid_segment`, `marketing.snapshot_changed`, `marketing.no_eligible_recipients`, `marketing.channel_not_configured`.

## UI
`/marketing`: campaign list. Campaign page: segment builder (chips for whitelisted filters), audience preview card (eligible / excluded by reason), content editor (markdown with preview) with revision history and from P6 a "Draft with AI" button, then submit for approval.

## Tests (must exist)
- `test_customer_without_explicit_marketing_consent_excluded`
- `test_suppressed_contact_excluded_at_preview_and_rechecked_at_execution`
- `test_content_edit_after_submit_requires_new_approval`
- `test_unsubscribe_appends_withdrawn_consent_and_suppression`
- `test_segment_definition_rejects_unknown_fields`

## As built (P6-01, 2026-11-03)
- **Dependencies:** `org` (business name and slug for the e-mail footer and link) and `scheduling` (the "had an appointment for service" filter) were added to the graph; `crm.public` gained `customer_ids_where`, `audience_candidates` and `normalize_contact`, `scheduling.public` gained `customer_ids_with_service`.
- **Segments** need at least one clause (all clauses must match). Extra keys, unknown fields and operators not allowed for a field are rejected with HTTP 422. "Last contact" uses the last interaction, or the creation date when the customer never interacted.
- **Exclusion order** (each person counted once): archived, no e-mail address, no marketing consent, suppressed. Consent means the latest marketing record for the channel is `granted`.
- **Tables:** `campaigns` has `submitted_snapshot_id` (set at submit) and `approved_snapshot_id` (set when sending starts) instead of one column. `suppressions`, `campaign_revisions` and `audience_members` are append-only for the application role.
- **Submit** freezes the audience (snapshot and members), moves the campaign to `in_review` and creates the proposal. **Any edit** of the message, name or audience while in review withdraws the proposal and returns the campaign to draft; a proposal that is rejected, expired or cancelled also returns it to draft. Sent, cancelled and approved campaigns are locked (`marketing.campaign_locked`).
- **Sending** re-checks, for every member just before its message is queued, that the latest consent is still `granted`, the primary address still exists and is not suppressed. The proposal result reports `enqueued`, `skipped_no_longer_eligible` and `skipped_suppressed`. Messages use the new `marketing_campaign` template, the channel from `notifications.email_channel()` (`simulated` until `SMTP_URL` is set, then `email`) and the idempotency key `campaign:{id}:{customer}`.
- **Unsubscribe** token: HMAC-SHA256 of `unsub1|business|customer|email` with `APP_SECRET_KEY`, no expiry, tied to its business. The public link answers a plain page, is rate limited, adds a suppression and one `withdrawn` consent record (not another one if repeated). Changing `APP_SECRET_KEY` invalidates links already sent.
- **API additions:** `POST /marketing/campaigns/{id}/cancel`; the preview takes `offset` and `limit` and returns the page in `members`.
- **UI** (`/marketing`, `/marketing/:id`): campaign list, a segment builder with chips (no free-form query), audience preview with exclusion reasons and paging, message editor with plain-text preview and revision history, submit and cancel, and the "do not e-mail" list. The "Draft with AI" button is **not** built (task P6-02, after kickoff).

Campaign mutation and execution acquire proposal before campaign locks. Execution rechecks proposal identity,
revision ID and audience snapshot hash while holding the campaign lock before enqueueing anything.
A replaced submission invalidates an older approved job even when its content and audience happen to be identical.
During downgrade from the business-hardening migration, erasure suppressions become `manual` suppressions
before the older reason constraint is restored; suppressed addresses remain excluded.
