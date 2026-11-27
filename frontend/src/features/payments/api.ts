// Online payments through Stripe: the business's connected account and its readiness (backend module `payments`).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { Schemas } from "@/api/types"
import { useSession } from "@/app/session-context"

export const CONNECTION_READ = "payments.connection.read"
export const CONNECTION_MANAGE = "payments.connection.manage"

export type PaymentConnection = Schemas["PaymentConnectionOut"]

const KEY = ["payments", "stripe"] as const

export function usePaymentConnection() {
  const { permissions } = useSession()
  return useQuery({
    queryKey: KEY, enabled: permissions.has(CONNECTION_READ),
    queryFn: async () => unwrap(await api.GET("/api/v1/payments/stripe")),
  })
}

function usePaymentsMutation<T, R>(fn: (data: T) => Promise<R>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSettled: async () => { await qc.invalidateQueries({ queryKey: KEY }) } })
}

export function useStartOnboarding() {
  return usePaymentsMutation(async (country: string) => unwrap(await api.POST("/api/v1/payments/stripe/onboarding", { body: { country } })))
}
export function useRefreshAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/payments/stripe/refresh")),
    onSuccess: (data) => { qc.setQueryData(KEY, data) },
    onError: async () => { await qc.invalidateQueries({ queryKey: KEY }) },
  })
}
export function useDisconnectStripe() {
  return usePaymentsMutation(async (version: number) => unwrap(await api.POST("/api/v1/payments/stripe/disconnect", { body: { version } })))
}

export type PaymentSettings = { reminder_links?: boolean; draft_refunds?: boolean }

/** What Tandem does on its own with online payments (phase 5): pay links in reminders, drafted surplus refunds. */
export function usePaymentSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ version, ...settings }: PaymentSettings & { version: number }) =>
      unwrap(await api.PATCH("/api/v1/payments/stripe/settings", { body: { version, ...settings } })),
    onSuccess: (data) => { qc.setQueryData(KEY, data) },
    onError: async () => { await qc.invalidateQueries({ queryKey: KEY }) },
  })
}

export type Payout = Schemas["PayoutOut"]

/** Stripe payouts to the business's bank, newest first (phase 4). Tandem never starts or changes one. */
export function usePayouts(enabled: boolean) {
  const { permissions } = useSession()
  return useQuery({
    queryKey: ["payments", "payouts"] as const, enabled: enabled && permissions.has(CONNECTION_READ),
    queryFn: async () => unwrap(await api.GET("/api/v1/payments/payouts")),
  })
}

export const LINK_SHARE = "payments.link.share"
export type InvoiceOnlinePayments = Schemas["InvoiceOnlinePaymentsOut"]

const invoiceKey = (invoiceId: string) => ["payments", "invoice", invoiceId] as const

export function useInvoiceOnlinePayments(invoiceId: string) {
  const { permissions } = useSession()
  return useQuery({
    queryKey: invoiceKey(invoiceId), enabled: permissions.has(LINK_SHARE),
    queryFn: async () => unwrap(await api.GET("/api/v1/payments/invoices/{invoice_id}", { params: { path: { invoice_id: invoiceId } } })),
  })
}

export function usePayLink(invoiceId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (reset: boolean) => unwrap(reset
      ? await api.POST("/api/v1/payments/invoices/{invoice_id}/link/reset", { params: { path: { invoice_id: invoiceId } } })
      : await api.POST("/api/v1/payments/invoices/{invoice_id}/link", { params: { path: { invoice_id: invoiceId } } })),
    onSettled: async () => { await qc.invalidateQueries({ queryKey: invoiceKey(invoiceId) }) },
  })
}

export const REFUND_PROPOSE = "payments.refund.propose"
export type RefundOption = Schemas["RefundOption"]

/** Asks for a card refund. Nothing is sent until an owner or admin approves it in Approvals. */
export function useCardRefund(invoiceId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (option: RefundOption) => unwrap(await api.POST("/api/v1/payments/invoices/{invoice_id}/card-refunds", {
      params: { path: { invoice_id: invoiceId } },
      body: { request_id: option.request_id, source: option.source, invoice_refund_id: option.invoice_refund_id ?? null },
    })),
    onSettled: async () => { await qc.invalidateQueries({ queryKey: invoiceKey(invoiceId) }) },
  })
}
