import type { Schemas } from "@/api/types"

// Generated request/response types for the AI endpoints present in schema.d.ts.
export type AiStatus = Schemas["AiStatus"]
export type AiPolicy = AiStatus["policy"]
export type RunOut = Schemas["RunOut"]
export type RunStatus = RunOut["status"]
export type RunPage = Schemas["Page_RunOut_"]
export type UsageToday = Schemas["UsageToday"]
export type UsageDay = Schemas["UsageDayOut"]
export type UsageOut = Schemas["UsageOut"]
export type ThreadOut = Schemas["CopilotThreadOut"]
export type ThreadDetailOut = Schemas["CopilotThreadDetailOut"]
export type ThreadMessageOut = Schemas["ThreadMessageOut"]
export type ThreadCreate = Schemas["ThreadCreate"]
export type MessageCreate = Schemas["MessageCreate"]
export type ThreadSummary = Schemas["ThreadSummary"]
export type SummaryOut = Schemas["SummaryOut"]
export type BriefingOut = Schemas["BriefingOut"]

/** §8/§14 SSE payload: OpenAPI describes text/event-stream as unknown, without a payload model. */
export interface StepEvent {
  kind: "step" | "proposal" | "final" | "error"
  run_id: string
  tool: string | null
  status_text: string | null
  proposal_id: string | null
  proposal_title: string | null
  text: string | null
  error_code: string | null
}

// Request and response types of the AI endpoints that are not in the generated schema yet.
//
// Written BY HAND from docs/roadmap/ai/contracts.md §11 (output models), §13 and §14 (HTTP API).
// When each endpoint lands in src/api/schema.d.ts, replace its type with Schemas["<Name>"] and
// its aiRequest call in api.ts with typed api.GET/POST + unwrap.
/** §14 POST /campaigns/{id}/draft */
export interface CampaignDraftOut {
  run_id: string
  revision_id: string
}

/** §11 SupplierQuoteExtract and §14 ExtractOut */
export interface SupplierQuoteLineExtract {
  item_id: string
  unit_price: string | null
  quantity_available?: string | null
}
export interface SupplierQuoteExtract {
  lines: SupplierQuoteLineExtract[]
  currency: string | null
  lead_time_days: number | null
  min_quantity?: string | null
  valid_until?: string | null
  notes?: string
}
export interface ExtractIn {
  supplier_id: string
  text: string
}
export interface ExtractOut {
  run_id: string
  extract: SupplierQuoteExtract
  verified: boolean
  unverified_fields: string[]
  proposal_id: string | null
}

/** §14 POST /procurement/items/{item_id}/request-quotes */
export interface RequestQuotesOut {
  run_id: string
  proposal_ids: string[]
}
