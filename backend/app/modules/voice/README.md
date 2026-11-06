# voice · M14

POST /voice/transcriptions requires ai.assistant.use. Streaming bounded in-memory multipart (4 MiB/120 s),
container signature checks, provider duration tolerance 125 s. Never creates a message or sends anything.
Returns editable text + raw_text + duration_s + polished + warnings; person must confirm in the calling UI.
voice_sessions: metadata only, forced business RLS; no audio/transcript/dictionary stored.
Idempotency claims scoped to business/user/input/permissions; text replay in process memory, 10 minutes/128 entries.
Cache misses return voice.replay_unavailable; active claims return voice.operation_in_progress. No duplicate inference.
30 new requests per user/hour via platform limiter. Permission-scoped dictionary via ai.public.
ai owns STT routing, reservations, metadata recording and writer polish/guards; local-only policies apply.
Registered export and erasure handlers; 90-day bounded cleanup uses ai.assistant_retention's existing daily job.
No events, tools, proposals, executors, TTS or frontend in this card.
