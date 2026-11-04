# conversations (M04)

Spec: [M04](../../../../docs/roadmap/modules/M04-conversations.md).

The public surface exposes thread reads, replies, conversation creation, customer linking, internal notes, handoff
and counts. `get_message_context(db, ctx, message_id) -> MessageContext` requires `conversations.thread.read` and
returns `conversation_id`, `channel`, `author_kind`, `body`, `customer_id`, `visitor_name`, `visitor_email`,
`visitor_phone`, and the message's `created_at`. Missing messages and messages from another business both raise
`NotFound("conversations.message_not_found")`.

Visitor contacts are server-side workflow context. This helper does not expose a new HTTP endpoint, write records,
or invoke an inference service.
