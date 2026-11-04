import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { SalesDocument, SalesDocumentCreate, SalesDocumentUpdate } from "@/api/types"
import { useSession } from "@/app/session-context"
import { destination, READ, WRITE, type DocumentFamily, type SalesAction } from "./model"

const LIST = { quotes: "/api/v1/sales/quotes", orders: "/api/v1/sales/orders", invoices: "/api/v1/sales/invoices" } as const
const DETAIL = { quotes: "/api/v1/sales/quotes/{document_id}", orders: "/api/v1/sales/orders/{document_id}", invoices: "/api/v1/sales/invoices/{document_id}" } as const
const ACTIONS = {
  send: "/api/v1/sales/quotes/{document_id}/send", accept: "/api/v1/sales/quotes/{document_id}/accept",
  decline: "/api/v1/sales/quotes/{document_id}/decline", expire: "/api/v1/sales/quotes/{document_id}/expire",
  convert: "/api/v1/sales/quotes/{document_id}/convert", confirm: "/api/v1/sales/orders/{document_id}/confirm",
  fulfil: "/api/v1/sales/orders/{document_id}/fulfil", cancel: "/api/v1/sales/orders/{document_id}/cancel",
  invoice: "/api/v1/sales/orders/{document_id}/invoice", issue: "/api/v1/sales/invoices/{document_id}/issue",
  "mark-paid": "/api/v1/sales/invoices/{document_id}/mark-paid", credit: "/api/v1/sales/invoices/{document_id}/credit",
} as const
export const salesKeys = {
  all: ["sales"] as const,
  list: (family: DocumentFamily, status: string) => ["sales", family, "list", status] as const,
  detail: (family: DocumentFamily, id: string) => ["sales", family, "document", id] as const,
}
export function useSalesDocuments(family: DocumentFamily, status: string) {
  const { permissions } = useSession()
  return useInfiniteQuery({
    queryKey: salesKeys.list(family, status), enabled: permissions.has(READ),
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => unwrap(await api.GET(LIST[family], { params: { query: { status: status === "overdue" ? undefined : status || undefined, overdue: status === "overdue" || undefined, cursor: pageParam, limit: 50 } } })),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}
export function usePaymentReminder(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: async (body: { body: string; version: number }) => unwrap(await api.POST("/api/v1/sales/invoices/{document_id}/reminders", { params: { path: { document_id: id } }, body })),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["approvals"] }) },
  })
}
export function useSalesDocument(family: DocumentFamily, id: string) {
  const { permissions } = useSession()
  return useQuery({ queryKey: salesKeys.detail(family, id), enabled: permissions.has(READ),
    queryFn: async () => unwrap(await api.GET(DETAIL[family], { params: { path: { document_id: id } } })),
  })
}
function useSalesMutation<T>(family: DocumentFamily, fn: (v: T) => Promise<SalesDocument>, stock = false) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: async (d) => {
    qc.setQueryData(salesKeys.detail(family, d.id), d)
    await Promise.all([qc.invalidateQueries({ queryKey: salesKeys.all }), ...(stock ? [qc.invalidateQueries({ queryKey: ["inventory"] })] : [])])
  } })
}
export function useCreateDocument(family: "quotes" | "invoices") {
  return useSalesMutation(family, async (body: SalesDocumentCreate) => unwrap(await api.POST(LIST[family], { body })))
}
export function useUpdateDocument(family: DocumentFamily, id: string) {
  return useSalesMutation(family, async (body: SalesDocumentUpdate) => unwrap(await api.PATCH(DETAIL[family], { params: { path: { document_id: id } }, body })))
}
export function useSalesAction(family: DocumentFamily, id: string, action: SalesAction) {
  return useSalesMutation(destination(family, action), async (version: number) => unwrap(await api.POST(ACTIONS[action], { params: { path: { document_id: id } }, body: { version } })), family === "orders")
}
export function useInvoiceFromAppointment() {
  return useSalesMutation("invoices", async (id: string) => unwrap(await api.POST("/api/v1/sales/invoices/from-appointment/{appointment_id}", { params: { path: { appointment_id: id } } })))
}

/** The API exposes the source only on the target. Check every customer page before offering a single-use conversion. */
export function useConversionTarget(family: DocumentFamily, d: SalesDocument) {
  const { permissions } = useSession()
  const eligible = (family === "quotes" && d.status === "accepted") || (family === "orders" && ["confirmed", "fulfilled"].includes(d.status))
  const target = family === "quotes" ? "orders" : "invoices"
  return useQuery({ queryKey: ["sales", "conversion", family, d.id], enabled: eligible && permissions.has(WRITE),
    queryFn: async () => {
      let cursor: string | undefined
      do {
        const page = unwrap(await api.GET(LIST[target], { params: { query: { customer_id: d.customer_id, limit: 200, cursor } } }))
        const found = page.items.find((item) => (family === "quotes" ? item.source_quote_id : item.source_order_id) === d.id)
        if (found) return found
        cursor = page.next_cursor ?? undefined
      } while (cursor)
      return null
    },
  })
}
