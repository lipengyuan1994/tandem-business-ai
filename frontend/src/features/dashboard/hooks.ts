import { useQuery } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"

export function useToday() {
  return useQuery({ queryKey: ["dashboard", "today"], queryFn: async () => unwrap(await api.GET("/api/v1/dashboard/today")), refetchInterval: 60_000 })
}

/** Nav badges (Inbox, Approvals); polled every 30 s (09 application shell). */
export function useCounters(enabled: boolean) {
  return useQuery({ queryKey: ["dashboard", "counters"], enabled, queryFn: async () => unwrap(await api.GET("/api/v1/dashboard/counters")), refetchInterval: 30_000 })
}
