# AI screens (AI-13)

Staff-facing AI features use the proposal lifecycle; Activity distinguishes rule evaluation from current execution.
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
| `ActionCard` + `DeliveryStatus` | saved chat proposals, Activity and proposal detail; revision-bound Confirm, local collapse, delivery countdown and confirmed Stop | backend card eligibility; Stop rechecks action approver / settings writer |
| `WorkQueuePage` / `ExceptionsPage` | `/ai/work-queue`, `/ai/exceptions`, AI navigation | `approvals.proposal.read` |
| `TeamPage`, `TeamActivity`, `ChainPage`, `AgentMention` | Team/detail/chain routes, measured depth, trigger chips, agent filters; assistant uses minimal addressable discovery ([P10 gaps](../../../../docs/roadmap/ai/cards/P10-gaps.md)); [P10-B3 evidence](../../../../docs/roadmap/ai/cards/P10-B3-screens.md) | Team: `approvals.proposal.read`; discovery: `ai.assistant.use`; edits: `org.settings.write` |
| `CustomerCareSettings`, `CustomerCareCase`, `CustomerCareProposal` | Team detail, Work and Approvals; [P10-B2 evidence](../../../../docs/roadmap/ai/cards/P10-B2-screens.md) | settings: `org.settings.write`; resolve: `conversations.proposal.approve` |
| `ActivityPage` | `/ai/activity`, AI navigation | `approvals.proposal.read`; result link also needs `inventory.reorder.read` |
| `AiSettingsPage` | `/settings/ai` (tab "AI") | `ai.settings.read` (edit: `ai.settings.write` + `org.settings.write`) |
| `AiDisabledBanner` | panel, summary dialog, paste dialog, settings | — (reads `GET /ai/status`) |
| `useDictation` + `DictationButton` | opt-in `Textarea dictation={{ context, entity_ids, mode }}`; assistant, Inbox, knowledge, staff prose/notes | `ai.assistant.use`, supported microphone/codec, available `stt` |

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

Dictation inserts editable text without submitting. Toggle or hold-to-talk; hold Alt+Space in the focused field,
release to stop; Esc cancels. Mono audio stays in bounded memory, stops at 120 seconds, with one same-key network
retry. STT off reasons appear in AI settings. See [P10-D screens](../../../../docs/roadmap/ai/cards/P10-D-screens.md)
for privacy, error handling, tests, offline Playwright instructions and verification limits.

## Tests

`sse.test.ts` (parser, stream state), `assistant.test.tsx` (panel), `features.test.tsx` (summary, badge, briefing, paste,
quotes), `settings.test.tsx`. E2E: `e2e/ai.e2e.ts` (needs the dev fake provider) and `/settings/ai` in `e2e/a11y.e2e.ts`.

P10-A0 part 2 uses generated types and `api.GET/PUT/POST` in `autonomy-api.ts`. Settings use the server-returned
version/state (including forced shadow for new rules), a reload dialog on 409 and a confirmed business pause.
Activity uses cursor pagination, business-local time, plain-language outcomes/reasons and version-correct grantors.
`autonomy.test.tsx` covers settings, permissions, pause, Activity and links; approvals tests cover policy attribution;
notification tests cover the rule explanation. No model or provider calls are needed for these deterministic screens.

P10-A1 uses generated `ChatCard`, `ChatConfirmIn`, `WorkItem`, `WorkPage`, `ExceptionPage`, and outbox types
in `actions-api.ts`. Cards poll execution/delivery transitions; the scoped proposal outbox supplies Stop ids
and `not_before` dates. Work queue uses `stop_until` (including retry scheduling), cursor pages and business-local
instants; reply-by remains a calendar date. Exceptions has no total/count badge in this API version.
Autonomy includes versioned `outbound_send_delay_seconds` (0–900 seconds, displayed in minutes, default 2).
`actions.test.tsx` covers cards, refusal reasons, exact confirm payload, reload, delivery, Stop races, queues,
permissions and settings through MSW. No provider calls or additional dependencies.

P10-C1 part 3 adds Front-desk replies to the existing Autonomy rule UI. Save needs
`conversations.proposal.approve`; the page still needs `org.settings.write`. Reply-specific fallback codes have
plain-language explanations on Autonomy, Activity and Exceptions. `web_reply` queue rows use the conversations
Stop endpoint; cancelled rows show the backend cancellation reason. Existing execution status remains separate.
`withheld`/`published` delivery states are labelled, and reply cards/proposals continue polling while withheld.

`ReplyGrounding.tsx` renders immutable reply payload citations, overall verdict and per-claim verdict/source on
proposal detail and reply action cards. Cards fetch the existing proposal detail and check the revision matches.
Knowledge reads need thread-read permission; missing permissions/titles/checks stay explicit. Citation titles resolve
from the exact immutable article version and links open that version's comparison. No inference runs in the UI.
`front-desk.test.tsx` covers evidence, Autonomy permissions/reasons, Exceptions and queued web-reply Stop through MSW.
