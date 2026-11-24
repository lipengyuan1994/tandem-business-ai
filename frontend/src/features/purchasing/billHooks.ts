import { useRef } from "react"
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import { useSession } from "@/app/session-context"
import { BILL_READ, filterStatuses, type BillFilter, type BillInput, type BillPaymentInput } from "./billModel"

export function useBills({ filter, purchaseOrderId }: { filter: BillFilter; purchaseOrderId?: string }) {
  const { permissions } = useSession()
  return useInfiniteQuery({
    queryKey: ["purchasing", "bills", filter, purchaseOrderId ?? null], enabled: permissions.has(BILL_READ), initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => unwrap(await api.GET("/api/v1/purchasing/bills", { params: { query: { limit: 50, cursor: pageParam, status: filterStatuses(filter), purchase_order_id: purchaseOrderId } } })),
    getNextPageParam: (p) => p.next_cursor ?? undefined,
  })
}
export function useBill(id: string) {
  const { permissions } = useSession()
  return useQuery({ queryKey: ["purchasing", "bill", id], enabled: permissions.has(BILL_READ), queryFn: async () => unwrap(await api.GET("/api/v1/purchasing/bills/{bill_id}", { params: { path: { bill_id: id } } })) })
}
export function usePayablesAging(asOf: string) {
  const { permissions } = useSession()
  return useQuery({ queryKey: ["purchasing", "payables-aging", asOf], enabled: permissions.has(BILL_READ), queryFn: async () => unwrap(await api.GET("/api/v1/purchasing/payables/aging", { params: { query: asOf ? { as_of: asOf } : {} } })) })
}
export function usePOBilling(purchaseOrderId: string | undefined) {
  const { permissions } = useSession()
  return useQuery({
    queryKey: ["purchasing", "po-billing", purchaseOrderId ?? null], enabled: !!purchaseOrderId && permissions.has(BILL_READ),
    queryFn: async () => unwrap(await api.GET("/api/v1/purchasing/purchase-orders/{purchase_order_id}/billing", { params: { path: { purchase_order_id: purchaseOrderId! } } })),
  })
}
function useBillMutation<T, R>(fn: (data: T) => Promise<R>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["purchasing"] }) } })
}
export function useSaveBill(id?: string) {
  const key = useRef(crypto.randomUUID())
  return useBillMutation(async (body: BillInput & { version?: number }) => {
    if (!id) {
      const { version: _version, ...input } = body
      const created = unwrap(await api.POST("/api/v1/purchasing/bills", { headers: { "Idempotency-Key": key.current }, body: input }))
      key.current = crypto.randomUUID()
      return created
    }
    const { supplier_reference, bill_date, due_date, total_minor, notes } = body
    return unwrap(await api.PATCH("/api/v1/purchasing/bills/{bill_id}", { params: { path: { bill_id: id } }, body: { version: body.version!, supplier_reference, bill_date, due_date, total_minor, notes } }))
  })
}
/** One idempotency key per dialog, so a retried click after a lost response cannot pay twice. */
export function useRecordBillPayment(id: string) {
  const key = useRef(crypto.randomUUID())
  return useBillMutation(async (body: BillPaymentInput) => {
    const result = unwrap(await api.POST("/api/v1/purchasing/bills/{bill_id}/payments", { params: { path: { bill_id: id } }, headers: { "Idempotency-Key": key.current }, body }))
    key.current = crypto.randomUUID()
    return result
  })
}
export function useVoidBill(id: string) {
  return useBillMutation(async ({ version, reason }: { version: number; reason?: string | null }) => unwrap(await api.POST("/api/v1/purchasing/bills/{bill_id}/void", { params: { path: { bill_id: id } }, body: { version, reason } })))
}
export function useVoidBillPayment(id: string) {
  return useBillMutation(async ({ paymentId, version }: { paymentId: string; version: number }) => unwrap(await api.POST("/api/v1/purchasing/bills/{bill_id}/payments/{payment_id}/void", { params: { path: { bill_id: id, payment_id: paymentId } }, body: { version } })))
}
