# conversations (M04)

Spec: [M04](../../../../docs/roadmap/modules/M04-conversations.md).

The public surface exposes thread reads, replies, conversation creation, customer linking, internal notes, handoff
and counts. `get_message_context(db, ctx, message_id) -> MessageContext` requires `conversations.thread.read` and
returns `conversation_id`, `channel`, `author_kind`, `body`, `customer_id`, `visitor_name`, `visitor_email`,
`visitor_phone`, and the message's `created_at`. Missing messages and messages from another business both raise
`NotFound("conversations.message_not_found")`.

Visitor contacts are server-side workflow context. This helper does not expose a new HTTP endpoint, write records,
or invoke an inference service.

`has_internal_note_marker(db, ctx, conversation_id, marker) -> bool` requires thread-read permission and checks an
assistant internal-note suffix with a scoped, indexed thread query returning at most one id; it never loads history.

P10-C1: `ai_mode`, `ai_generation`, `ai_paused_at` enforce human ownership. Inbound messages carry their insertion
generation. `take_over`/`hand_back` require thread.write; `ensure_ai_active` checks read permission and ownership,
optionally under the customer→thread lock. Direct staff replies take over; approved AI replies retain AI ownership.
`request_handoff` pauses AI, invalidates open assistant reply proposals, and notifies the assignee or all active users
with conversations.proposal.approve. Every manual reply/takeover advances generation even while human.
Cancellation skips locked proposals; an ownership event provides cleanup and
the executor checks mode/generation before posting. Staff-origin proposals are preserved.

Visitor POST `/public/{slug}/chat/handoff` uses the scoped visitor cookie and message/IP limits. Repeated requests
in one human episode have no duplicate message, event or notification. Business-local hours choose the deterministic
system acknowledgement; org setting handoff_after_hours_text customizes closed-hours copy. Public thread/profile
DTOs return ai_greeting. Part 3 adds public-message is_system for distinct widget acknowledgement styling.

Published events now include conversations.handoff_requested and conversations.ai_ownership_changed (generation).
Dependencies include identity for eligible recipient lookup; conversations never imports AI or knowledge.

C1 part 2: `replies.py` retains manual/email delivery and withholds every AI-origin executor web reply (including
booking replies). `publication.py` schedules `conversations.publish_reply` at org's outbound send delay. Public reads
filter to published rows and cursor on release time; staff messages expose publication_state, publish_at,
cancellation_reason, sending_label and stop_url. POST `/messages/{id}/stop` requires action approve or settings.write.
Subsequent visitor/customer inputs advance ai_generation under the thread lock, independent of clocks/UUID ordering.
Stop/publish serialize customer→conversation→message; publishing rechecks auto mode, source inbound, generation,
closed/restricted/erased state. Stopped/cancelled rows never become public. Cancellations join Exceptions through
`publication_queue_items`; registered delivery reader projects withheld/published/stopped/failed independently.
Executor callback `autonomy_reasons` requires intake provenance, clean safety, supported grounding and no bundle.
`GroundingResult` is a public DTO in the immutable reply payload. Manual proposals stay human-decided.
Public `retention_eligible_conversations(ctx) -> Select[UUID]` supplies a scoped ID subquery for derived-data
cleanup; it excludes persistent restricted_at holds even after the data profile changes.

P10-B2: `send_care_reply` requires the generation captured in the approved payload and an explicit due time;
it rejects missing/stale generations. Care proposals participate in ownership invalidation. Publication's Care
guard allows a human-approved complaint reply in human mode while freshness, restrictions, closure and Stop apply.
Customer linking calls the registered Care hook under customer→conversation locks to bind open anonymous cases.

P10-C2b: `embed.py` adds `/public/{slug}/embed/chat{,/messages,/handoff}` using the same services, disclosure,
projection and quotas. Capability in `X-Embed-Token` (iframe sessionStorage/memory), never an embed cookie;
mutations require our exact application Origin. Existing first-party Secure/HttpOnly/Lax cookie is unchanged.
