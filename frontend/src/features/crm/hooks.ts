import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { ConsentIn, CustomerCreate, CustomerUpdate } from "@/api/types"

export const crmKeys = {
  all: ["crm"] as const,
  list: (q: string) => ["crm", "customers", q] as const,
  detail: (id: string) => ["crm", "customer", id] as const,
  timeline: (id: string) => ["crm", "timeline", id] as const,
  consents: (id: string) => ["crm", "consents", id] as const,
  notes: (id: string) => ["crm", "notes", id] as const,
  match: (name: string) => ["crm", "match", name] as const,
}

export function useCustomers(q: string, status: "active" | "archived" = "active") {
  return useInfiniteQuery({
    queryKey: [...crmKeys.list(q), status],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) =>
      unwrap(await api.GET("/api/v1/crm/customers", { params: { query: { q: q || undefined, cursor: pageParam, limit: 50, status } } })),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}

const path = (id: string) => ({ params: { path: { customer_id: id } } })

export function useCustomer(id: string) {
  return useQuery({ queryKey: crmKeys.detail(id), queryFn: async () => unwrap(await api.GET("/api/v1/crm/customers/{customer_id}", path(id))) })
}

export function useTimeline(id: string) {
  return useQuery({
    queryKey: crmKeys.timeline(id),
    queryFn: async () => unwrap(await api.GET("/api/v1/crm/customers/{customer_id}/timeline", { params: { path: { customer_id: id }, query: { limit: 100 } } })),
  })
}

export function useConsents(id: string) {
  return useQuery({ queryKey: crmKeys.consents(id), queryFn: async () => unwrap(await api.GET("/api/v1/crm/customers/{customer_id}/consents", path(id))) })
}

export function useNotes(id: string) {
  return useQuery({ queryKey: crmKeys.notes(id), queryFn: async () => unwrap(await api.GET("/api/v1/crm/customers/{customer_id}/notes", path(id))) })
}

/** Name search used by "link customer" pickers (trigram match, best first). */
export function useCustomerMatch(name: string) {
  return useQuery({
    queryKey: crmKeys.match(name),
    enabled: name.trim().length >= 2,
    queryFn: async () => unwrap(await api.GET("/api/v1/crm/customers/match", { params: { query: { name } } })),
  })
}

function useCrmMutation<TVars, TOut>(fn: (vars: TVars) => Promise<TOut>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: crmKeys.all }) })
}

export function useCreateCustomer() {
  return useCrmMutation(async (body: CustomerCreate) => unwrap(await api.POST("/api/v1/crm/customers", { body })))
}

export function useUpdateCustomer(id: string) {
  return useCrmMutation(async (body: CustomerUpdate) => unwrap(await api.PATCH("/api/v1/crm/customers/{customer_id}", { ...path(id), body })))
}

export function useRecordConsent(id: string) {
  return useCrmMutation(async (body: ConsentIn) => unwrap(await api.POST("/api/v1/crm/customers/{customer_id}/consents", { ...path(id), body })))
}

export function useAddNote(id: string) {
  return useCrmMutation(async (body: { body: string; pinned: boolean }) =>
    unwrap(await api.POST("/api/v1/crm/customers/{customer_id}/notes", { ...path(id), body })))
}

export function useAddContactPoint(id: string) {
  return useCrmMutation(async (body: { type: "email" | "phone"; value: string; is_primary: boolean }) =>
    unwrap(await api.POST("/api/v1/crm/customers/{customer_id}/contact-points", { ...path(id), body })))
}

export function useRemoveContactPoint() {
  return useCrmMutation(async (contactPointId: string) =>
    unwrap(await api.DELETE("/api/v1/crm/contact-points/{contact_point_id}", { params: { path: { contact_point_id: contactPointId } } })))
}

export function useSetTags(id: string) {
  return useCrmMutation(async (tags: string[]) => unwrap(await api.PUT("/api/v1/crm/customers/{customer_id}/tags", { ...path(id), body: { tags } })))
}

export function useRestoreCustomer(id: string) {
  return useCrmMutation(async (version: number) => unwrap(await api.POST("/api/v1/crm/customers/{customer_id}/restore", { ...path(id), body: { version } })))
}

export function useArchiveCustomer(id: string) {
  return useCrmMutation(async (version: number) => unwrap(await api.POST("/api/v1/crm/customers/{customer_id}/archive", { ...path(id), body: { version } })))
}
