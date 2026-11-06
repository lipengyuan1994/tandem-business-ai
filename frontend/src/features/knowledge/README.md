# Knowledge screens (P10-C1 part 3)

Lazy routes `/knowledge`, `/knowledge/new`, `/knowledge/:articleId`; navigation and reads need
`conversations.thread.read`. Creating/editing/publishing/archiving and suggestion triage need `org.settings.write`.
Generated API types and `api.GET/POST/PATCH` live in `hooks.ts`; no model calls or extra dependencies.

List/search has status and visibility filters and badges, bounded to 100 results (API has no cursor).
Editor creates drafts or appends versions; Publish and Archive are version-bound, confirmed actions.
Unsaved edits disable publication/archive. Optimistic conflicts retain edits until explicit reload.
Markdown preview renders a documented small subset through escaped React nodes, without raw HTML or images.
History compares two immutable snapshots, including tags/title/body and business-local creation times.
`?version=N` selects the cited historical version for comparison; the editable form remains the latest version.

Suggestions prefill the article title from the question. Saving atomically converts the suggestion into a draft;
opening/cancelling the editor does not change the backend. Dismiss uses the suggestion's expected version.

`knowledge.test.tsx` uses MSW for list/search/filter, create/edit, explicit publish/archive, version comparison,
suggestions, permissions, conflicts/unsaved publication protection and safe Markdown preview.
