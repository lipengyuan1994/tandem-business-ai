import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { StockMovement } from "@/api/types"

export const inventoryKeys = {
  all: ["inventory"] as const,
  levels: (belowOnly: boolean) => ["inventory", "levels", belowOnly] as const,
  item: (id: string) => ["inventory", "item", id] as const,
  suggestions: ["inventory", "suggestions"] as const,
  drafts: ["inventory", "drafts"] as const,
}

export function useLevels(belowOnly = false) {
  return useQuery({
    queryKey: inventoryKeys.levels(belowOnly),
    queryFn: async () => unwrap(await api.GET("/api/v1/inventory/levels", { params: { query: { below_reorder: belowOnly || undefined } } })),
  })
}

export function useItemStock(id: string) {
  return useQuery({
    queryKey: inventoryKeys.item(id),
    queryFn: async () => unwrap(await api.GET("/api/v1/inventory/items/{item_id}", { params: { path: { item_id: id } } })),
  })
}

export function useSuggestions(enabled = true) {
  return useQuery({ queryKey: inventoryKeys.suggestions, enabled, queryFn: async () => unwrap(await api.GET("/api/v1/inventory/reorder-suggestions")) })
}

export function useReorderDrafts(enabled = true) {
  return useQuery({ queryKey: inventoryKeys.drafts, enabled, queryFn: async () => unwrap(await api.GET("/api/v1/inventory/reorder-drafts")) })
}

function useInventoryMutation<TVars, TOut>(fn: (vars: TVars) => Promise<TOut>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: inventoryKeys.all }) })
}

/** A stock count: the server records the difference as a count correction, or nothing when the count matches. */
export function useCountStock() {
  return useInventoryMutation(async (v: { item_id: string; stock_location_id: string | null; counted: string; note: string | null }) =>
    unwrap(await api.POST("/api/v1/inventory/counts", { body: v })))
}

export function useSaveReorderRule(itemId: string) {
  return useInventoryMutation(async (body: { reorder_point: string; target_level: string; lead_time_days: number; supplier_hint: string | null }) =>
    unwrap(await api.PUT("/api/v1/inventory/reorder-rules/{item_id}", { params: { path: { item_id: itemId } }, body })))
}

/** Turns a reorder draft into a purchase order for one supplier; the draft is then marked sent to purchasing. */
export function useConvertDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { draft_id: string; supplier_id: string; version: number }) =>
      unwrap(await api.POST("/api/v1/purchasing/purchase-orders/from-draft", { body: v })),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: inventoryKeys.all }), qc.invalidateQueries({ queryKey: ["purchasing"] })]),
  })
}

export type AdjustReason = "adjustment_gain" | "adjustment_loss" | "count_correction" | "receipt" | "return"

/** Stock never gets overwritten: an adjustment is a signed movement with a reason (M07). Losses are sent negative. */
export function useAdjustStock() {
  return useInventoryMutation(async (v: { item_id: string; quantity: number; reason: AdjustReason; note: string | null }) =>
    unwrap(await api.POST("/api/v1/inventory/movements", { body: { ...v, quantity: String(v.quantity) } })))
}

export function useCreateReorderDraft() {
  return useInventoryMutation(async (lines: { item_id: string; quantity: string; reason_text: string | null }[]) =>
    unwrap(await api.POST("/api/v1/inventory/reorder-drafts", { body: { lines, note: null } })))
}

/**
 * Ledger rows oldest → newest with the balance after each movement (the API returns the newest 100, newest first).
 * The opening balance is derived from the current on-hand quantity, so the numbers stay right when older
 * movements are not in the list.
 */
export function withRunningBalance(movements: StockMovement[], onHand: number): (StockMovement & { balance: number })[] {
  const opening = onHand - movements.reduce((sum, m) => sum + Number(m.quantity), 0)
  let balance = opening
  return [...movements]
    .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at))
    .map((m) => ({ ...m, balance: (balance += Number(m.quantity)) }))
}
