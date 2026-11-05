# AI screens (AI-13)

Staff-facing AI features. AI changes use the proposal lifecycle; reorder drafts can receive delegated policy approval.
Activity distinguishes the rule evaluation from the proposal’s current execution result.
All copy is in `en.json` under `ai.*`; `AI can make mistakes; you approve every change` is fixed.

| Piece | Where it shows | Needs |
|---|---|---|
| `AssistantLauncher` + `AssistantPanel` | top bar button and Ctrl/Cmd+J; slide-over, full screen on phones | `ai.assistant.use` |
| `SummariseButton` | Inbox thread header (dialog with summary, open questions, link to the reply draft) | `ai.assistant.use` |
| `AiBadge`, evidence note | Approvals list and proposal detail, when `origin_kind === "assistant"` | — |
| `BriefingCard` | Today, hidden when AI is off | `ai.assistant.use` |
| `PasteSupplierReplyButton` / `PasteSupplierReplyDialog` | quote request page while status is `sent` | `ai.assistant.use` + `purchasing.po.write` |
| `AskAiQuotesButton` | inventory item page when stock is at or below the reorder point and 2+ suppliers with an e-mail address sell it | `ai.assistant.use` + `purchasing.po.read/write` |
| `AutonomyPage` | `/ai/autonomy`, AI navigation | `org.settings.write` (save also needs `inventory.proposal.approve`) |
| `ActivityPage` | `/ai/activity`, AI navigation | `approvals.proposal.read`; result link also needs `inventory.reorder.read` |
| `AiSettingsPage` | `/settings/ai` (tab "AI") | `ai.settings.read` (edit: `ai.settings.write` + `org.settings.write`) |
| `AiDisabledBanner` | panel, summary dialog, paste dialog, settings | — (reads `GET /ai/status`) |

Files: `api.ts` (hooks of contracts §16), `http.ts` (plain fetch for endpoints not generated yet), `stream.ts` and `sse.ts`
(copilot stream: `fetch` POST with the CSRF header, `TextDecoder`, events split on blank lines, aborted on unmount),
`types.ts`, `text.ts` (error and "AI is off" wording), `extract.ts` (which extracted values were unverified).

## Generated and hand-written API types

Status, runs (`Page_RunOut_` cursor pages), usage, copilot threads (plain arrays), thread detail, thread/message
requests, conversation summaries and briefings use generated `Schemas["…"]` aliases in `types.ts`.
Their JSON requests use `api.GET/POST` + `unwrap` in `api.ts`. Copilot types use `CopilotThreadOut` /
`CopilotThreadDetailOut`; the Inbox's generated `ThreadOut` is a different model. Run costs are microdollars
(`cost_micros / 1_000_000` for USD), and pagination sends the opaque `next_cursor` as a typed query parameter.

Only procurement extract/request-quotes and campaign draft endpoints still use hand-written request/response types
and `aiRequest` in `http.ts`, following contracts §11/§14. When an endpoint is in `src/api/schema.d.ts`, replace
its type with `Schemas["…"]` and its call with `api.GET/POST` + `unwrap`.

The copilot message stream stays on `fetch` with CSRF. OpenAPI has no SSE payload model, so `StepEvent` remains a
hand-written protocol type: `event: <kind>\ndata: <JSON>\n\n`, with every payload field present and unused fields
null. Pre-stream problem+json errors use `code`; 429 `platform.rate_limited` (with `Retry-After`) shows the hourly
limit message without automatically retrying the POST. Tests mock every AI endpoint with MSW (`testing.ts`);
no test calls a backend or a model.

## Tests

`sse.test.ts` (parser, stream state), `assistant.test.tsx` (panel), `features.test.tsx` (summary, badge, briefing, paste,
quotes), `settings.test.tsx`. E2E: `e2e/ai.e2e.ts` (needs the dev fake provider) and `/settings/ai` in `e2e/a11y.e2e.ts`.

P10-A0 part 2 uses generated types and `api.GET/PUT/POST` in `autonomy-api.ts`. Settings use the server-returned
version/state (including forced shadow for new rules), a reload dialog on 409 and a confirmed business pause.
Activity uses cursor pagination, business-local time, plain-language outcomes/reasons and version-correct grantors.
`autonomy.test.tsx` covers settings, permissions, pause, Activity and links; approvals tests cover policy attribution;
notification tests cover the rule explanation. No model or provider calls are needed for these deterministic screens.
