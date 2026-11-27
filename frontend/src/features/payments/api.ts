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
