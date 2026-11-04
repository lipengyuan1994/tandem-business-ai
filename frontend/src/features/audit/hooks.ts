import { useInfiniteQuery } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"

export interface AuditFilters {
  action_prefix?: string
  actor_kind?: string
  target_id?: string
}

export function useAuditEvents(filters: AuditFilters) {
  return useInfiniteQuery({
    queryKey: ["audit", "events", filters],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) =>
      unwrap(await api.GET("/api/v1/audit/events", { params: { query: { ...clean(filters), cursor: pageParam, limit: 50 } } })),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}

function clean(f: AuditFilters): AuditFilters {
  return Object.fromEntries(Object.entries(f).filter(([, v]) => v)) as AuditFilters
}

export function exportUrl(filters: AuditFilters): string {
  const q = new URLSearchParams(clean(filters) as Record<string, string>).toString()
  return `/api/v1/audit/export.csv${q ? `?${q}` : ""}`
}
