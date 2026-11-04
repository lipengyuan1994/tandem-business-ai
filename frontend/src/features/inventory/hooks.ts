import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { StockMovement } from "@/api/types"

export const inventoryKeys = {
  all: ["inventory"] as const,
  levels: (belowOnly: boolean) => ["inventory", "levels", belowOnly] as const,
  item: (id: string) => ["inventory", "item", id] as const,
  suggestions: ["inventory", "suggestions"] as const,
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

function useInventoryMutation<TVars, TOut>(fn: (vars: TVars) => Promise<TOut>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: inventoryKeys.all }) })
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
