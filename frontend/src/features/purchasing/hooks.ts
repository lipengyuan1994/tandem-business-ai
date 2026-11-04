import { useRef } from "react"
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { CatalogItem, Schemas } from "@/api/types"
import { useSession } from "@/app/session-context"
import { READ } from "./model"

export function useSuppliers() {
  const { permissions } = useSession()
  return useQuery({ queryKey: ["purchasing", "suppliers"], enabled: permissions.has(READ), queryFn: async () => {
    const rows: Schemas["SupplierOut"][] = []
    let cursor: string | undefined
    do {
      const page = unwrap(await api.GET("/api/v1/purchasing/suppliers", { params: { query: { limit: 200, cursor } } }))
      rows.push(...page.items); cursor = page.next_cursor ?? undefined
    } while (cursor)
    return rows
  } })
}
export function useOrders() {
  const { permissions } = useSession()
  return useInfiniteQuery({ queryKey: ["purchasing", "orders"], enabled: permissions.has(READ), initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => unwrap(await api.GET("/api/v1/purchasing/purchase-orders", { params: { query: { limit: 50, cursor: pageParam } } })), getNextPageParam: (p) => p.next_cursor ?? undefined,
  })
}
export function useOrder(id: string) {
  const { permissions } = useSession()
  return useQuery({ queryKey: ["purchasing", "order", id], enabled: permissions.has(READ), queryFn: async () => unwrap(await api.GET("/api/v1/purchasing/purchase-orders/{purchase_order_id}", { params: { path: { purchase_order_id: id } } })) })
}
export function useSuggestions() {
  const { permissions } = useSession()
  return useQuery({ queryKey: ["purchasing", "suggestions"], enabled: permissions.has(READ), queryFn: async () => unwrap(await api.GET("/api/v1/purchasing/reorder-suggestions")) })
}
export function usePurchasingPickers() {
  return useQuery({ queryKey: ["purchasing", "pickers"], queryFn: async () => {
    const business = unwrap(await api.GET("/api/v1/org/business"))
    const items: CatalogItem[] = []
    let cursor: string | undefined
    do {
      const page = unwrap(await api.GET("/api/v1/catalog/items", { params: { query: { kind: "product", limit: 200, cursor } } }))
      items.push(...page.items.filter((i) => i.status === "active" && i.is_stock_tracked)); cursor = page.next_cursor ?? undefined
    } while (cursor)
    return { currency: business.currency, items }
  } })
}
function usePurchasingMutation<T, R>(fn: (data: T) => Promise<R>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: async () => {
    await Promise.all([qc.invalidateQueries({ queryKey: ["purchasing"] }), qc.invalidateQueries({ queryKey: ["inventory"] })])
  } })
}
export function useSaveSupplier(id?: string) {
  return usePurchasingMutation(async (body: Schemas["SupplierIn"] & { version?: number }) => id
    ? unwrap(await api.PATCH("/api/v1/purchasing/suppliers/{supplier_id}", { params: { path: { supplier_id: id } }, body: { ...body, version: body.version! } }))
    : unwrap(await api.POST("/api/v1/purchasing/suppliers", { body })))
}
export function useSaveOrder(id?: string) {
  return usePurchasingMutation(async (body: Schemas["POCreate"] & { version?: number }) => id
    ? unwrap(await api.PATCH("/api/v1/purchasing/purchase-orders/{purchase_order_id}", { params: { path: { purchase_order_id: id } }, body: { version: body.version!, notes: body.notes, lines: body.lines } }))
    : unwrap(await api.POST("/api/v1/purchasing/purchase-orders", { body })))
}
const ACTIONS = { approve: "/api/v1/purchasing/purchase-orders/{purchase_order_id}/approve", send: "/api/v1/purchasing/purchase-orders/{purchase_order_id}/send", cancel: "/api/v1/purchasing/purchase-orders/{purchase_order_id}/cancel" } as const
export function useOrderAction(id: string) {
  return usePurchasingMutation(async ({ action, version }: { action: keyof typeof ACTIONS; version: number }) => unwrap(await api.POST(ACTIONS[action], { params: { path: { purchase_order_id: id } }, body: { version } })))
}
export function useReceive(id: string) {
  return usePurchasingMutation(async ({ body, idempotencyKey }: { body: Schemas["ReceiptIn"]; idempotencyKey: string }) => unwrap(await api.POST("/api/v1/purchasing/purchase-orders/{purchase_order_id}/receipts", { params: { path: { purchase_order_id: id } }, headers: { "Idempotency-Key": idempotencyKey }, body })))
}

export function useQuoteRequests() {
  const { permissions } = useSession()
  return useInfiniteQuery({ queryKey: ["purchasing", "quote-requests"], enabled: permissions.has(READ), refetchInterval: 30_000, initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => unwrap(await api.GET("/api/v1/purchasing/quote-requests", { params: { query: { limit: 50, cursor: pageParam } } })), getNextPageParam: (p) => p.next_cursor ?? undefined,
  })
}
export function useQuoteRequest(id: string) {
  const { permissions } = useSession()
  return useQuery({ queryKey: ["purchasing", "quote-request", id], enabled: permissions.has(READ), queryFn: async () => unwrap(await api.GET("/api/v1/purchasing/quote-requests/{quote_request_id}", { params: { path: { quote_request_id: id } } })), refetchInterval: 30_000 })
}
export function useQuoteComparison(id: string) {
  const { permissions } = useSession()
  return useQuery({ queryKey: ["purchasing", "comparison", id], enabled: permissions.has(READ), queryFn: async () => unwrap(await api.GET("/api/v1/purchasing/quote-requests/{quote_request_id}/comparison", { params: { path: { quote_request_id: id } } })), refetchInterval: 30_000 })
}
export function useSupplierLink() {
  // The link is a capability: no cache, nothing invalidated, and the mutation result is dropped as soon as it is used.
  return useMutation({ gcTime: 0, mutationFn: async ({ requestId, supplierId }: { requestId: string; supplierId: string }) =>
    unwrap(await api.POST("/api/v1/purchasing/quote-requests/{quote_request_id}/invites/{supplier_id}/link", { params: { path: { quote_request_id: requestId, supplier_id: supplierId } } })) })
}
export function useLateOrders(enabled = true) {
  const { permissions } = useSession()
  return useQuery({ queryKey: ["purchasing", "late-orders"], enabled: enabled && permissions.has(READ), queryFn: async () => unwrap(await api.GET("/api/v1/purchasing/late-purchase-orders")), refetchInterval: 60_000 })
}
export type ProcurementAction =
  | { kind: "request"; body: Schemas["SendQuoteRequests"] }
  | { kind: "record"; body: Schemas["RecordSupplierQuote"] }
  | { kind: "po"; body: Schemas["CreatePOFromQuote"] }
  | { kind: "followup"; body: Schemas["SupplierFollowup"] }
export function useProcurementAction() {
  // Reusing an identical submission after a lost response must reuse its proposal key.
  const attempts = useRef(new Map<string, string>())
  return usePurchasingMutation(async (action: ProcurementAction) => {
    const payload = JSON.stringify(action)
    let key = attempts.current.get(payload)
    if (!key) { key = crypto.randomUUID(); attempts.current.set(payload, key) }
    const headers = { "Idempotency-Key": key }
    switch (action.kind) {
      case "request": return unwrap(await api.POST("/api/v1/purchasing/quote-requests", { headers, body: action.body }))
      case "record": return unwrap(await api.POST("/api/v1/purchasing/quote-requests/{quote_request_id}/quotes", { headers, params: { path: { quote_request_id: action.body.quote_request_id } }, body: action.body }))
      case "po": return unwrap(await api.POST("/api/v1/purchasing/purchase-orders/from-quote", { headers, body: action.body }))
      case "followup": return unwrap(await api.POST("/api/v1/purchasing/supplier-followups", { headers, body: action.body }))
    }
  })
}
