# M10 · notifications

| Field | Value |
|---|---|
| Release / phase | MVP (outbox, simulated and in-app) · P2 / v1 (SMTP) · P8 |
| Tier / kind | **A** for outbox and delivery states; B for in-app UI and templates · PRE |
| Depends on | platform, org, identity, approvals |
| Owns tables | `outbox_messages`, `delivery_attempts`, `in_app_notifications`, `message_templates` |

## Purpose
Everything that leaves the system, or reaches staff outside the page they are on: the transactional **outbox** for customer-facing messages (with honest delivery states), and in-app notifications for staff (new proposals, handoffs, alerts).

## Tables
`outbox_messages` (exempt from row-level security; see [05](../05-data-model.md#tenancy-and-row-level-security))

| Column | Type | Notes |
|---|---|---|
| business_id | uuid | |
| channel | text | `web_chat` \| `email` \| `sms` (later) \| `simulated` |
| recipient | text | **PII**: email or phone; null for `web_chat` |
| template_key | text | `conversation_reply`, `appointment_confirmation`, … |
| variables | jsonb | Rendered at delivery time |
| idempotency_key | text | unique |
| related_type, related_id | text, uuid | e.g. `message`, `campaign` |
| proposal_id | uuid | nullable |
| status | text | `pending` \| `sending` \| `delivered` \| `failed` \| `unknown` |
| attempts, next_attempt_at, locked_until | | |
| provider_message_id | text | |
| last_error | text | |
| delivered_at | timestamptz | |
| resolved_by, resolution | uuid, text | For manually resolved `unknown` |

`delivery_attempts` — one row per adapter call: `outbox_message_id`, `started_at`, `finished_at`, `result`, `error`.

`in_app_notifications` — `user_id`, `kind`, `title`, `body`, `link`, `read_at`.

`message_templates` — **deferred to v1.** MVP templates live in code (`notifications/templates.py`) and are rendered with Jinja2's `ImmutableSandboxedEnvironment` + `StrictUndefined`; only plain data reaches templates.

## Business rules
1. `enqueue_message` only inserts, in the caller's transaction. Delivery is always in the worker.
1a. **As built:** each message is delivered by a `notifications.deliver` job in three short transactions (pending→sending committed; adapter call with no transaction held; record result). A cron reaper turns messages stuck in `sending` past a 5-minute lease into `unknown`, so a crash can never cause a silent duplicate.
2. Adapter contract: `send(message, idempotency_key) → Delivered(provider_id) | RetryableError | PermanentError | Unknown`.
3. `web_chat` adapter: marks delivered immediately (the visitor polls their thread). `simulated` adapter: marks delivered with `provider_message_id='simulated-…'`; the UI labels it **Simulated**. `smtp` (v1): `Unknown` on timeout after the DATA phase.
4. Retryable: backoff (1 min, 5 min, 30 min, 2 h), maximum 5 attempts, then `failed`.
5. `unknown` is never retried automatically. Staff with the permission resolve it with *Mark delivered* or *Retry anyway*; both are audited.
6. Every status change publishes `notifications.delivery_status_changed` (`related_type`, `related_id`, `status`).
7. Recipients are rechecked before email or SMS sends for suppression (v1, through marketing's suppression list passed in variables at enqueue time; transactional messages to existing customers are allowed).
8. In-app notifications: notifications subscribes to `approvals.proposal_created` (notifies users who can approve that type). Higher modules call `notify_users` directly, for example conversations on handoff. System alerts (dead jobs, backup age, budget) go to admins.

## Public interface (`public.py`)
```python
def enqueue_message(db, ctx, *, channel, recipient, template_key, variables, idempotency_key,
                    related_type, related_id, proposal_id=None) -> OutboxRef
def notify_users(db, ctx, user_ids: list[UUID], *, kind, title, body, link) -> None
def outbox_status_counts(db, ctx) -> dict[str, int]    # dashboard / system page
def email_channel() -> str                              # "email" if SMTP_URL is set, else "simulated" (see As built)
def simulated_delivery() -> ContextManager[None]        # inside the block email_channel() is always "simulated" (the seed)
```

## API
| Method | Path | Permission |
|---|---|---|
| GET | `/notifications` | session (own) |
| POST | `/notifications/{id}/read`, `/notifications/read-all` | session |
| GET | `/notifications/outbox` | `notifications.outbox.read` (`?status=unknown`) |
| POST | `/notifications/outbox/{id}/resolve` | `notifications.outbox.resolve` `{resolution: delivered \| retry}` |

## Permissions
`notifications.outbox.read`, `notifications.outbox.resolve` (owner, admin, operator).

## Events
Published: `notifications.delivery_status_changed`. Subscribed: `approvals.proposal_created`.

## Jobs
`notifications.deliver` (claims pending outbox rows; runs continuously in the worker loop).

## Error codes
`notifications.unknown_template`, `notifications.invalid_recipient`, `notifications.not_unknown_state`.

## UI
Bell menu with unread notifications; "Delivery issues" list on the System page and in the proposal detail.

## Tests (must exist)
- `test_enqueue_rolls_back_with_business_transaction`
- `test_retryable_backoff_then_failed`
- `test_unknown_not_retried_and_resolution_audited`
- `test_idempotency_key_unique_prevents_duplicate_enqueue`
- `test_template_sandbox_blocks_attribute_access`
- `test_approvers_notified_on_proposal_created`

## Out of scope
SMS and WhatsApp providers, push notifications, per-user notification preferences (v1).


## As built (2026-11-03)

- P8-02 uses standard-library `smtplib`, with 10-second socket connect/send/read timeouts and system certificate/hostname verification. `smtp://` defaults to STARTTLS on 587; `smtps://` or port 465 uses implicit TLS. Explicit plaintext is restricted to localhost for tests. Invalid `SMTP_URL` prevents startup without printing the URL; `from` or `SMTP_FROM` supplies the sender. Only host/port are logged; provider response text is never persisted.
- SMTP messages are multipart plain text and HTML from the same sandboxed template, with autoescaped HTML, static inline styles and no remote images/tracking. Message-ID hashes the outbox idempotency key; Date/From/To are set. `org.get_public_email` supplies `public_profile.email` for Reply-To. `marketing_campaign` includes List-Unsubscribe and one-click POST headers when its unsubscribe URL exists.
- 4xx and pre-DATA connection errors use existing retry/backoff; 5xx are permanent. Timeout or connection loss after the server accepts DATA is `unknown`, never automatically retried. A final SMTP 250 is delivered even if the connection later closes. SMS refuses explicitly.
- Owner/admin `POST /api/v1/admin/test-email {to}` validates the address, rate limits to five per business per fixed hour, enqueues `test_message` and audits the outbox ID without recipient content. HTTP 202 means queued, not delivered. System shows configured/host only, plus a test dialog and result toast. The existing owner/admin system-read permission gates both endpoint and service.
- Keycloak SMTP is **manual**: first-start realm import is skipped for existing realms, so staging password-bearing JSON would not cover upgrades. [Mail setup](../../runbooks/email.md) maps the same URL to Keycloak Email settings and documents install/upgrade/rotation, invitation and Forgot-password tests. No SMTP password is placed in realm files. Real provider delivery and Keycloak mail flows require operator verification; local tests do not establish them.
- `aiosmtpd` is test-only (ADR-019), bound to random loopback ports; existing simulated notifications need no SMTP service. No AI code or runtime dependency was added.


## As built — one channel rule for ordinary e-mail (2026-11-03)

- `notifications.public.email_channel()` returns `email` when `SMTP_URL` is set and `simulated` otherwise. It is the single
  implementation of "an empty `SMTP_URL` leaves normal notifications simulated" ([mail runbook](../../runbooks/email.md),
  M12 rule 6). Marketing campaigns, purchasing supplier mail (`quote_request`, `supplier_followup`) and sales
  `payment_reminder` pass it as `channel=`; they no longer name a channel themselves. Without SMTP these messages are
  delivered by the simulated adapter (no delivery issue, labelled Simulated); with SMTP they use the real adapter.
- The channel is a property of the queued row. Adding SMTP does not change messages already queued as `simulated`; removing it
  makes messages still pending as `email` fail with "email delivery is not configured".
- Deliberately not covered: the System "Send test e-mail" always uses `email` and is refused until SMTP is configured, and
  conversation replies use `web_chat` or `simulated` only (no e-mail replies yet, M04).
- Spec change: the public interface gained `email_channel()`; no graph change (marketing, purchasing and sales already
  declare notifications).
- `notifications.public.simulated_delivery()` (2026-11-03) is a context manager (a `ContextVar`, so it is private to the
  code that entered it) inside which `email_channel()` answers `simulated` whatever `SMTP_URL` says. The demo seed runs
  inside it: its suppliers and customers are example.com addresses and a real provider would bounce the mail. Rows are
  created `simulated`; none is rewritten afterwards. The previous state returns when the block ends, also on an error.
  An explicit `channel="email"` is not affected. Spec change: the public interface gained `simulated_delivery()`.
