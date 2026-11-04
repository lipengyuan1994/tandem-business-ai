# M04 · conversations

| Field | Value |
|---|---|
| Release / phase | MVP · P3 |
| Tier / kind | B · PRE (AI suggestions appear in P5 through the ai module) |
| Depends on | platform, org, crm, approvals, notifications |
| Owns tables | `conversations`, `messages`, `visitor_tokens` |

## Purpose
A shared inbox. Threads come from the public chat widget, from staff logging a phone call or walk-in, and (v1) from email. Staff assign, reply and close threads. Publishing `message_received` is what lets the AI do intake later. This module never calls the AI.

## Tables
`conversations`

| Column | Type | Null | Notes |
|---|---|---|---|
| customer_id | uuid | yes | Composite FK; null until matched |
| channel | text | no | `web_chat` \| `phone_log` \| `walk_in` \| `email` (v1) \| `voice` (stretch) |
| subject | text | yes | |
| status | text | no | `open` \| `waiting_on_staff` \| `waiting_on_customer` \| `closed` |
| assigned_to | uuid | yes | user id |
| last_message_at | timestamptz | no | |
| visitor_name, visitor_email, visitor_phone | text | yes | **PII**: what a visitor typed before matching |
| contact_consent_at | timestamptz | yes | Original public submission time, only when ticked with an e-mail address |
| contact_consent_text | text | yes | Versioned server-owned label; paired with contact_consent_at |
| handoff_requested_at | timestamptz | yes | |

`messages`

| Column | Type | Null | Notes |
|---|---|---|---|
| conversation_id | uuid | no | Composite FK |
| direction | text | no | `inbound` \| `outbound` \| `internal` |
| author_kind | text | no | `visitor` \| `customer` \| `staff` \| `assistant` \| `system` |
| author_user_id | uuid | yes | |
| body | text | no | **PII possible**; max 8,000 chars |
| delivery_status | text | yes | For outbound: mirrors the outbox (`pending` \| `delivered` \| `failed` \| `unknown` \| `simulated`) |
| outbox_message_id | uuid | yes | |
| source_ref | text | yes | Unique per business when present (deduplicates imports and email) |
| proposal_id | uuid | yes | Reply created by an approved proposal |

`visitor_tokens` — `conversation_id`, `token_hash`, `expires_at`, `revoked_at`.

## Business rules
1. A public chat starts a conversation with a visitor token ([06](../06-security-and-privacy.md#authentication-flow-keycloak-backend-for-frontend)). Visitors read and write **only** their own conversation.
2. Every inbound message publishes `conversations.message_received` (payload: `conversation_id`, `message_id`, `channel`, `is_first_message`).
3. Outbound staff replies are created by `send_reply`. It writes the message and enqueues delivery through `notifications.enqueue_message` (channel `web_chat` delivers by making the message visible to the visitor; `email` v1). Status changes reach the message through an event from notifications.
4. An assistant never posts an outbound message directly. Only the `conversations.send_reply` executor does, after approval.
5. Internal notes (`direction='internal'`) are never visible to visitors.
6. Status moves to `waiting_on_staff` on inbound and `waiting_on_customer` on outbound. Closing is manual. A new inbound message reopens the thread.
7. Linking a conversation to a customer writes a timeline entry and fills `customer_id`. Explicit public contact consent is promoted once through CRM's public API when the visitor e-mail matches an active customer e-mail contact (including secondary contacts), using normalized values. This also covers customer creation and linking in the booking executor.
8. Public endpoints are rate limited: 10 messages per minute per token, 60 per hour per IP, and 5 new conversations per hour per IP shared across businesses and both chat/appointment-request entry points. Reads allow 60 per minute per thread and 120 per minute per IP.
9. Chat and appointment requests use the optional label "You may contact me about this request by e-mail." A tick with an e-mail stores the original time and `v1: <label>` on the conversation. Unticked, omitted, or no e-mail records nothing. The client cannot supply evidence wording or time. Promotion grants only `email/service_updates`, source `form`, preserves the original time, and includes the conversation id and label in evidence; it never grants marketing consent. Staff see the agreement date. Export includes both fields; erasure clears them and regulated restriction retains them.

## Public interface (`public.py`)
```python
def get_thread(db, ctx, conversation_id, last_n: int = 20) -> ThreadDto           # messages oldest→newest, customer summary if linked
def get_message_context(db, ctx, message_id) -> MessageContext
def send_reply(db, ctx, conversation_id, body: str, *, proposal_id=None) -> MessageDto   # used by executor and UI
def link_customer(db, ctx, conversation_id, customer_id) -> None
def request_handoff(db, ctx, conversation_id, reason: str) -> None              # also calls notifications.notify_users
def add_internal_note(db, ctx, conversation_id, body: str, author_kind: str) -> MessageDto   # ai posts its staff note here
def count_open_by_status(db, ctx) -> dict[str, int]                               # dashboard
```

`MessageContext` contains `conversation_id`, `channel`, `author_kind`, `body`, `customer_id`, `visitor_name`,
`visitor_email`, `visitor_phone`, and message `created_at`. It requires `conversations.thread.read`;
missing or foreign-business messages raise `conversations.message_not_found`. Contact details remain server-side.

## API
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/conversations` | `conversations.thread.read` | `?status=&assigned_to=me&channel=` |
| POST | `/conversations` | `conversations.thread.write` | Staff logs a phone call or walk-in (first message inbound) |
| GET | `/conversations/{id}` | read | Thread with messages |
| PATCH | `/conversations/{id}` | write | Assign, status, subject, `customer_id` |
| POST | `/conversations/{id}/messages` | write | `{direction: outbound \| internal, body}` |
| POST | `/public/{slug}/chat` | public | Start: `{name, email?, phone?, message, consent_to_contact: bool}` → sets visitor cookie |
| GET | `/public/{slug}/chat` | visitor token | Own thread (no internal notes), at most 50 messages; optional `cursor` returns subsequent messages and `next_cursor` advances polling |
| POST | `/public/{slug}/chat/messages` | visitor token | Optional `cursor`; response uses the same bounded history page as GET |
| POST | `/public/{slug}/appointment-requests` | public | **As built: owned by scheduling (M06)**, which validates the service and windows, then calls `conversations.start_conversation` to create the conversation with a formatted first message; **never books directly** |

## Permissions
`conversations.thread.read`, `conversations.thread.write`, `conversations.proposal.approve`.

## Events
Published: `conversations.message_received`, `conversations.handoff_requested`, `conversations.conversation_linked`. Subscribed: `notifications.delivery_status_changed` (update `delivery_status`).

## Executors
`conversations.send_reply` (payload: `conversation_id`, `body`, `conversation_version`). `describe()` shows the full message text and recipient channel.

## Error codes
`conversations.not_found`, `conversations.closed`, `conversations.visitor_token_invalid`, `conversations.public_chat_disabled`, `conversations.message_too_long`.

## UI
`/inbox`: three panes (thread list with status tabs; thread view with composer and internal-note toggle; customer card with "link customer" search). Public widget `/p/:slug/chat` and `/p/:slug/request`, with a privacy notice that says plainly whether AI processes messages, taken from the business's `ai_policy`.

## Tests (must exist)
- `test_visitor_cannot_read_other_conversation_even_with_id`
- `test_internal_notes_hidden_from_visitor`
- `test_inbound_message_publishes_event_once`
- `test_public_rate_limits_enforced`
- `test_send_reply_enqueues_outbox_in_same_transaction`
- `test_appointment_request_creates_conversation_not_appointment`

## Out of scope
Email ingestion (v1), SMS/WhatsApp (later), attachments (v1 with files), typing indicators.

## As built (P3, 2026-11-03)
- The appointment-request endpoint lives in scheduling (it needs the service details from catalog); this module only provides `start_conversation`.
- The public widget is a separate Vite entry (`frontend/widget.html`, `src/widget/`) served at `/p/:slug/chat` and `/p/:slug/request`: about 74 KB gzip of JavaScript (budget 80 KB). Its privacy notice comes from `ai_policy` on `GET /public/{slug}/profile`. The widget polls the thread every 5 s.
- Inbox UI: `/inbox` and `/inbox/:conversationId`, with status tabs, an internal-note toggle and a "link customer" search (`/crm/customers/match`).

Public intake hardening:
- `public_chat_enabled` gates chat creation, visitor messages, and appointment requests; existing history remains readable.
- Appointment requests set the same scoped HttpOnly visitor cookie as chat creation and return `reply_url` to the chat page.
  The cookie selects the new request thread even if the browser previously used another chat.
- Standard erasure sets a durable `erased_at` marker, revokes visitor tokens (including matching visitor-only threads),
  and rejects stale writes under the conversation lock. Migration backfills previously erased threads.
- Customer locks precede conversation locks for mutations; multi-customer links acquire customers in UUID order.
  Composite booking-with-reply acquires both customer locks before booking.
- Delivery events project the locked authoritative outbox status, so delayed events cannot restore older states.
- The widget retains earlier messages while polling with `next_cursor`, and bounds its in-memory display to 200 messages.
