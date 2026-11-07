# knowledge (M19)

Spec: [M19](../../../../docs/roadmap/modules/M19-knowledge.md). P10-C1 + P10-C2a / EVENT.

Owns `kb_articles`, append-only `kb_article_versions`, and `kb_suggestions`, all with forced business RLS.
Explicit publish appends a timestamped snapshot; draft edits retain the earlier publication. Archive removes
retrieval. Widening internal→public hides the old publication until explicit publish. Versions have a partial
GIN index over simple-config title/body vectors. C2a adds public published `kb_chunks` (forced RLS, vector(1024),
HNSW cosine, exact body spans). Embeddings are supplied through the composition registry, without an AI import.
Public retrieval combines filtered FTS/cosine candidates by RRF; unavailable/failed embeddings use FTS only.
Internal staff search stays FTS. Chunks cannot authorize tools/rules; the C1 grounding gate is unchanged.

Public API: `search_public(db,ctx,query,limit=5) -> list[SearchHit]`,
`search_staff(db,ctx,query,limit=5) -> list[SearchHit]`, `suggest(db,ctx,conversation_id,question) -> None`.
Hits contain article_id/version/title and at most 700 passage characters. Public search filters before ranking.
Read requires conversations.thread.read; staff write/publish/archive/triage requires org.settings.write.

HTTP `/knowledge/articles`: list/search, create, detail with versions, edit, publish, archive.
`/knowledge/suggestions`: list, versioned dismiss or convert-to-draft (atomic status done + article reference).
`POST /knowledge/imports`: bounded, streaming multipart PDF in memory; a resource-limited child process extracts
text into unpublished drafts with sanitized filename/page provenance. No PDF bytes are retained. Limits: 10 MiB,
100 pages, 300,000 characters; encrypted/scanned/corrupt PDFs fail clearly. Linux requires RLIMIT_AS (fail closed).
Owner-approved native macOS development skips AS only, with one structured warning; CPU/wall/file/core/text caps
remain. No watchdog. Linux-image enforcement is reviewer-owned; see C2a card for focused verification.
`POST /knowledge/reindex` (write permission) schedules a business sweep; `POST /articles/{id}/unpublish` hides it.
Index status/error/model fields are exposed on article responses; route availability is checked for list/detail.
Suggestions redact email/phone contacts, deduplicate open questions, and participate in erasure/export.
Export includes all article versions and chunk metadata (no copied text or vectors). Erasure discards derived
chunks and increments the index generation, preventing an in-flight job restoring stale data. Customer erasure
tombstones questions linked to their customer/visitor
conversations through the pre-collected ErasureTarget links; unrelated questions and business article history stay.
Owner-written articles are business data, not automatic captures of customer messages; versions need no erasure.

Job/cron `knowledge.retention` runs daily per business: delete done/dismissed suggestions strictly older than
`retention_days_conversations` (default 730 days) since updated_at, excluding persistent conversation holds.
Batches of 500 use skip-locked rows and scoped,
coalesced one-minute continuations. Open questions and articles/versions stay; no schema or new setting is added.
Dependencies: conversations, org. No executors or domain events; publish/archive/unpublish are audited.
Nontransactional `ai.knowledge_index` embeds at most 32 chunks of one public publication, rechecks generation/model/
visibility, then atomically replaces chunks. Publish queues it; new publication/narrowing/archive/unpublish delete
old chunks immediately; version/article deletion cascades. `knowledge.reindex` handles 25 articles per job with
continuations; hourly per-business sweeps detect model changes and retry unavailable/failed work. Jobs expose
sanitized failures. Re-ranking is deferred. Full requirement evidence: [C2a](../../../../docs/roadmap/ai/cards/P10-C2a.md).
Seed adds five public Cycle Works articles, four Garden Store articles, and one internal guide per business.
Tests cover CRUD/publication/ranking, visibility/RLS/permissions, immutable versions, and suggestions.
Admin tests verify complete scoped export and actual customer/visitor erasure; retention tests cover settings,
boundary/recent/open rows, business isolation, bounded batches, continuation deduplication and missing scope.

P10-D: public.dictation_names(db, ctx, entity_ids) returns bounded permission/business-scoped names only.
