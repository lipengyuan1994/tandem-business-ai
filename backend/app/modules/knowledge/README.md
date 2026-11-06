# knowledge (M19)

Spec: [M19](../../../../docs/roadmap/modules/M19-knowledge.md). P10-C1 part 1 / EVENT.

Owns `kb_articles`, append-only `kb_article_versions`, and `kb_suggestions`, all with forced business RLS.
Explicit publish appends a timestamped snapshot; draft edits retain the earlier publication. Archive removes
retrieval. Widening internal→public hides the old publication until explicit publish. Versions have a partial
GIN index over simple-config title/body vectors; no extension, dependency, embedding or hosted call is added.

Public API: `search_public(db,ctx,query,limit=5) -> list[SearchHit]`,
`search_staff(db,ctx,query,limit=5) -> list[SearchHit]`, `suggest(db,ctx,conversation_id,question) -> None`.
Hits contain article_id/version/title and at most 700 passage characters. Public search filters before ranking.
Read requires conversations.thread.read; staff write/publish/archive/triage requires org.settings.write.

HTTP `/knowledge/articles`: list/search, create, detail with versions, edit, publish, archive.
`/knowledge/suggestions`: list, versioned dismiss or convert-to-draft (atomic status done + article reference).
Suggestions redact email/phone contacts, deduplicate open questions, and participate in erasure/export.
Export includes all article versions. Customer erasure tombstones questions linked to their customer/visitor
conversations through the pre-collected ErasureTarget links; unrelated questions and business article history stay.
Owner-written articles are business data, not automatic captures of customer messages; versions need no erasure.

Job/cron `knowledge.retention` runs daily per business: delete done/dismissed suggestions strictly older than
`retention_days_conversations` (default 730 days) since updated_at, excluding persistent conversation holds.
Batches of 500 use skip-locked rows and scoped,
coalesced one-minute continuations. Open questions and articles/versions stay; no schema or new setting is added.
Dependencies: conversations, org. No executors or domain events; publish/archive are audited.
Seed adds five public Cycle Works articles, four Garden Store articles, and one internal guide per business.
Tests cover CRUD/publication/ranking, visibility/RLS/permissions, immutable versions, and suggestions.
Admin tests verify complete scoped export and actual customer/visitor erasure; retention tests cover settings,
boundary/recent/open rows, business isolation, bounded batches, continuation deduplication and missing scope.
