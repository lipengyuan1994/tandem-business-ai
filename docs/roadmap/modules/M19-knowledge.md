# M19 · knowledge

| Field | Value |
|---|---|
| Release / phase | v1 · P10-C1 part 1 |
| Tier / kind | A · EVENT |
| Depends on | platform, conversations, org |
| Owns tables | `kb_articles`, `kb_article_versions`, `kb_suggestions` |

## Purpose
Owner-written text knowledge for staff and cited public front-desk answers (ADR-020 decision 4).
Search is PostgreSQL full-text search; no model, embedding, upload or external service is involved.

## Tables
All three tables use business scoping, forced RLS and composite business foreign keys.

| Table / column | Type | Null | Notes |
|---|---|---|---|
| articles: id, business_id, timestamps, version | standard scoped | no | Optimistic version for staff mutations |
| title, slug | text | no | Title ≤200; unique business/slug |
| visibility, status | text | no | public/internal; draft/published/archived |
| current_version, published_version | integer | published only | Latest editable revision vs published snapshot |
| created_by | uuid | yes | Staff author; null for synthetic tooling |
| versions: article_id, version_no | uuid, integer | no | Unique business/article/version number |
| title, body_markdown, tags | text, text, text[] | no | Body ≤20,000 chars, ≤20 tags of ≤50 chars |
| author, created_at, published_at | uuid, timestamps | author/published | Append-only app grants |
| search_vector | tsvector | no | Generated simple-config title/body; GIN on published snapshots |
| suggestions: question_text, conversation_id | text, uuid | no | Contact-redacted question ≤4,000 chars |
| status, article_id | text, uuid | article only | open/done/dismissed; conversion points to draft |

Only `simple` is used: existing migrations do not enable unaccent. Publication appends a snapshot with
`published_at`; drafts are never updated to mark publication. Editing a published article retains its old
published snapshot. Widening internal→public hides the old publication until explicit republish. Archive removes it from retrieval. Historical published snapshots stay immutable.

## Business rules
1. Staff reads require `conversations.thread.read`; writes/publish/archive/suggestion triage require
   `org.settings.write` (existing owner/admin permission, no new role bundle).
2. Create/edit append versions; mutations lock the article and check its optimistic version.
3. Publish explicitly appends a snapshot, moves published_version, audits; archive audits and hides retrieval.
4. `search_public` returns only current published snapshots of public/published articles in this business.
   Ranked passages are bounded, plain text; internal/draft/archived content never reaches intake.
5. Staff search can include internal published articles with read permission. Only an authenticated copilot
   run on behalf of staff can choose this path; model arguments cannot select visibility or identity.
6. Suggestions use the actual inbound question, redact email/phone contacts, deduplicate open questions per
   conversation, and may be dismissed or converted atomically to a draft with status done.
7. Business export includes articles, every immutable version and suggestions. Customer erasure tombstones
   suggestion questions linked to their conversations (including visitor contact matches), dismisses open ones
   and redacts identity needles elsewhere in this business. Business article content/versions are retained.
8. Daily per-business `knowledge.retention` removes done/dismissed suggestions strictly older than the existing
   `retention_days_conversations` setting (default 730 days), measured from updated_at. Open suggestions and
   article history remain. Persistent conversation retention holds are excluded, including after profile switches,
   via the conversations public ID subquery. Skip-locked 500-row batches yield to coalesced one-minute scoped continuations.

## Public interface (`public.py`)
```python
def search_public(db, ctx, query: str, limit: int = 5) -> list[SearchHit]: ...
def search_staff(db, ctx, query: str, limit: int = 5) -> list[SearchHit]: ...
def suggest(db, ctx, conversation_id, question: str) -> None: ...
```
SearchHit contains article_id, version, title, passages (≤2 fragments, ≤700 characters total).

## API
All paths below are under `/api/v1/knowledge`.

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/articles` | conversations read | List/search; visibility/status filters; bounded limit |
| GET | `/articles/{id}` | conversations read | Article with immutable versions |
| POST | `/articles` | org.settings.write | Create draft |
| PATCH | `/articles/{id}` | org.settings.write | New revision; expected article version |
| POST | `/articles/{id}/publish`, `/archive` | org.settings.write | Explicit, versioned |
| GET | `/suggestions` | conversations read | Status filter, bounded list |
| POST | `/suggestions/{id}/dismiss`, `/convert` | org.settings.write | Versioned; conversion creates draft |

## Permissions
Reuse `conversations.thread.read` and `org.settings.write`.

## Events
None published/subscribed. Publish/archive emit audit records.

## Executors and jobs
None. Articles are explicitly written/published by staff, never by AI.

## Error codes
`knowledge.not_found`, `knowledge.version_conflict`, `knowledge.slug_exists`, `knowledge.suggestion_closed`.

## UI
Part 1 supplies the API. Part 3 adds `/knowledge` list/search/filter, the versioned article editor with safe
Markdown preview, confirmed explicit publish/archive, immutable side-by-side history and suggestion triage.
Navigation/read access uses conversations.thread.read; authoring and triage use org.settings.write.
Citation links select historical versions with `?version=N`. See `frontend/src/features/knowledge/README.md`.

## Tests (must exist)
- CRUD, version retention, explicit publication, archive, search ranking and passage bounds.
- Public-only current publication, staff internal visibility, business RLS, read/write permissions.
- App-role update/delete denial on versions; suggestions redaction/dismiss/convert.

## Out of scope
Embeddings, PDF upload, semantic grounding checks, reply autonomy and web-chat publication delay.
