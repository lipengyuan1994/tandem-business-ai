# M03 · crm

| Field | Value |
|---|---|
| Release / phase | MVP · P3 |
| Tier / kind | B · PRE |
| Depends on | platform, approvals |
| Owns tables | `customers`, `contact_points`, `consent_records`, `customer_notes`, `tags`, `customer_tags`, `timeline_entries` |

## Purpose
The single customer record: people and organisations, how to reach them, what they consented to, notes, tags, and a timeline that other modules write to. Customer matching (finding candidates for an incoming message) is deterministic search, not AI.

## Tables
`customers`

| Column | Type | Null | Notes |
|---|---|---|---|
| kind | text | no | `person` \| `organization` |
| display_name | text | no | **PII** |
| given_name, family_name | text | yes | **PII** |
| organization_name | text | yes | |
| preferred_language | text | yes | BCP 47 |
| timezone | text | yes | IANA; defaults to business |
| source | text | no | `staff` \| `public_chat` \| `import` \| `assistant` |
| status | text | no | `active` \| `archived` \| `merged` \| `erased` |
| merged_into_id | uuid | yes | composite FK customers |
| last_interaction_at | timestamptz | yes | maintained by `add_timeline_entry` |
| created_by | uuid | yes | |

Indexes: trigram (`pg_trgm`) index on `lower(display_name)` for search. *(Enable `pg_trgm` in the P1 extensions migration.)*

`contact_points` — `customer_id`, `type` (`email` \| `phone`), `value` (**PII**), `value_normalized` (lower-case email / E.164 phone), `is_primary bool`, `verified_at`. Unique `(business_id, type, value_normalized)` among active customers, so the same email cannot belong to two active customers.

`consent_records` — **append-only** history. Columns: `customer_id`, `channel` (`email` \| `sms`), `purpose` (`marketing` \| `service_updates`), `status` (`granted` \| `denied` \| `withdrawn`), `source` (`form` \| `staff` \| `import` \| `unsubscribe_link`), `evidence` (text, e.g. "checkbox on public request form v1"), `recorded_by`, `recorded_at`. The current consent is the latest record per `(customer, channel, purpose)`. The view `current_consents` provides it.

`customer_notes` — `customer_id`, `body` (**PII** possible), `author_id`, `pinned bool`.

`tags` — `name` (unique per business), `color`. `customer_tags` — `(customer_id, tag_id)`.

`timeline_entries` — `customer_id`, `occurred_at`, `kind` (`message` \| `appointment` \| `reservation` \| `note` \| `campaign` \| `proposal` \| `order` \| `other`), `ref_module`, `ref_type`, `ref_id`, `summary` (≤ 200 chars). Unique `(business_id, ref_type, ref_id, kind)` to make writes idempotent.

## Business rules
1. Customers are never auto-merged. `find_customer_candidates` returns scored candidates. Exact normalised email or phone match → score 1.0; trigram name similarity ≥ 0.6 → score = similarity × 0.8.
2. **Marketing consent is never inferred** from bookings or conversations. Only explicit records count.
3. Consent records are never updated or deleted, only appended. The app role has INSERT/SELECT only.
4. Archived customers are hidden from default lists but keep their history. Merged customers redirect to `merged_into_id`.
5. Merging (v1) moves contact points, notes, tags and timeline to the survivor, marks the other `merged`, and calls `on_customer_merged` handlers through an event.
6. Erasure (v1, M18): replace PII columns with `"[erased]"`, delete contact points and notes, keep IDs and timeline kinds.

## Public interface (`public.py`)
```python
def get_customer_summary(db, ctx, customer_id) -> CustomerSummary   # id, display_name, kind, primary contact TYPES, tags, last_interaction_at, timezone
def get_customer_contact(db, ctx, customer_id, channel) -> ContactPointDto | None   # for delivering messages
def find_customer_candidates(db, ctx, *, name=None, email=None, phone=None, limit=5) -> list[CustomerCandidate]
def get_timeline(db, ctx, customer_id, limit=50) -> list[TimelineEntryDto]
def add_timeline_entry(db, ctx, entry: TimelineEntryIn) -> None        # idempotent
def current_consent(db, ctx, customer_id, channel, purpose) -> ConsentStatus
def record_request_contact_consent(db, ctx, customer_id, *, email, recorded_at, evidence) -> None  # normalized active e-mail match, idempotent under customer lock; grants email/service_updates/form using original time
def list_marketing_eligible(db, ctx, channel, customer_ids: list[UUID] | None = None) -> list[EligibleContact]  # marketing uses this
def create_customer(db, ctx, data: CustomerCreate) -> CustomerSummary  # also used by the executor
def update_customer(db, ctx, customer_id, data: CustomerUpdate) -> CustomerSummary
```

## API
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/crm/customers` | `crm.customer.read` | `?q=&tag=&status=`; paginated |
| POST | `/crm/customers` | `crm.customer.write` | With contact points and optional consent records; `Idempotency-Key` |
| GET / PATCH | `/crm/customers/{id}` | read / write | |
| POST | `/crm/customers/{id}/archive` | `crm.customer.write` | |
| GET / POST | `/crm/customers/{id}/contact-points` | read / write | |
| PATCH / DELETE | `/crm/contact-points/{id}` | write | |
| GET / POST | `/crm/customers/{id}/consents` | `crm.consent.read` / `crm.consent.write` | POST appends a record |
| GET / POST | `/crm/customers/{id}/notes` | read / write | |
| GET | `/crm/customers/{id}/timeline` | read | |
| GET / POST | `/crm/tags`; PUT `/crm/customers/{id}/tags` | read / write | |
| GET | `/crm/customers/match` | read | `?email=&phone=&name=` → candidates (used by the UI and the AI tool) |

## Permissions
`crm.customer.read`, `crm.customer.write`, `crm.consent.read`, `crm.consent.write`, `crm.proposal.approve`.

## Events
Published: `crm.customer_created`, `crm.customer_updated`, `crm.consent_changed`, `crm.customer_merged` (v1).

## Executors
`crm.create_customer` (payload: `CustomerCreate`), `crm.update_customer` (payload: `customer_id`, `customer_version`, changes). `describe()` lists each field change.

## Error codes
`crm.customer_not_found`, `crm.customer_version_conflict`, `crm.contact_point_in_use`, `crm.invalid_email`, `crm.invalid_phone`, `crm.customer_archived`.

## UI
`/customers` (table: name, primary contact type, tags, last interaction; search box), `/customers/:id` (header with actions; tabs: Timeline, Appointments, Conversations, Notes, Consent; consent panel shows the history, never just a checkbox), create/edit dialog.

## Tests (must exist)
- `test_duplicate_normalized_email_rejected_for_active_customers`
- `test_candidates_exact_email_scores_one_and_name_similarity_ranked`
- `test_consent_is_append_only_and_current_is_latest`
- `test_marketing_eligibility_requires_explicit_granted_marketing_consent`
- `test_timeline_entry_idempotent`
- `test_executor_update_fails_on_stale_version`

## Out of scope
Companies with multiple contacts as separate entities (an organisation customer has contact points only in MVP); lead scoring; deduplication jobs.

Contact removal deactivates the row and clears its primary flag, preserving campaign audience history.
Ordinary contact reads, contact-based customer search, delivery and eligibility use only active contacts.
