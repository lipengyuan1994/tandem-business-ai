# AI-13 · Frontend AI features

Kind **EVENT** · Tier B · Size L · Depends on: AI-01 (start against mocks from contracts §14); integrate each piece when its backend card is DONE (AI-08, AI-10, AI-11, AI-12) · Blocks: demo

## Goal
Make the AI visible, understandable and safe to use: an assistant panel that streams its steps, one-click summaries and
reply drafts, AI badges and evidence on approvals, a briefing on Today, "paste supplier reply" in purchasing, clear
banners when AI is off, and an AI settings page in plain words.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/contracts.md` §14 (endpoints, SSE format) and §16 (hooks and components — implement these names).
2. `docs/roadmap/09-frontend.md` (rules) and `docs/roadmap/ai/testing.md` §4 (frontend part).
3. `frontend/src/features/marketing/` (template: hooks, pages, dialogs, tests) and `frontend/src/app/layout.tsx`.
4. `frontend/src/api/client.ts` (how requests and CSRF work) and `frontend/src/lib/errors.ts`.
5. When integrating: `backend/openapi.json` paths under `/api/v1/ai`.

## You build (`frontend/src/features/ai/`)
- `api.ts` hooks of §16. SSE: `fetch` POST with the CSRF header, read `response.body` with a `TextDecoder`, split on blank
  lines, parse `event:`/`data:`; abort on unmount; surface `error` events.
- `AssistantPanel` (sheet on the right, Ctrl/Cmd+J and a top-bar button; full screen on phones): thread list
  (`useCopilotThreads`), new thread, input,
  streaming step list with `status_text`, proposal chips linking to `/approvals/$proposalId`, the fixed line "AI can make
  mistakes; you approve every change".
- `SummariseButton` in the Inbox thread view → shows the summary, open questions and "Reply draft created — review in
  Approvals" with a link.
- `AiBadge` and an "Evidence" list on proposal detail when `origin_kind === "assistant"` (field already in the approvals
  API response); also show the badge in the Approvals list.
- `BriefingCard` on Today (hidden when AI is off), `AiDisabledBanner` from `useAiStatus` on AI surfaces with the reason in
  plain words (not configured / policy / budget).
- `PasteSupplierReplyDialog` on a quote request page (PQ-02 screen): textarea → extract → `ExtractOut`: show each line
  and field, mark `unverified_fields`, say plainly when `verified` is false ("Some numbers could not be found in the text;
  enter this quote by hand"), link to the proposal when `proposal_id` is set.
- "Ask AI to request quotes" button on a low-stock item when it has ≥ 2 suppliers.
- `AiSettingsPage` at `/settings/ai` (owner/admin): policy explained in one sentence each, effective policy (regulated
  shown as locked), daily budget, usage chart (simple bars), run history table (no text, status, cost, latency).
- All strings in `en.json` under `ai.*`; terminology overrides apply; lazy routes; initial JS budget unchanged.

## Tests
Component tests with mocked fetch/streams: panel renders streamed steps and proposal chips; error event shows a message;
disabled banner reasons; summary flow; paste dialog shows unverified fields; settings page shows locked regulated policy.
Add `/settings/ai` to the a11y e2e list. One e2e with the dev `fake` provider: open the panel, ask, see a final answer.

## Done when
`pnpm exec tsc -b`, `pnpm lint`, `pnpm exec vitest run`, `make e2e`, `make check` pass; initial JS ≤ 250 KB gzip
(report the number); `HANDOFF.md` rewritten.
