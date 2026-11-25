// QuickBooks Online connection and sync log (backend module `accounting`).
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { Schemas } from "@/api/types"
import { useSession } from "@/app/session-context"

export const SYNC_READ = "accounting.sync.read"
export const SYNC_RUN = "accounting.sync.run"
export const CONNECTION_MANAGE = "accounting.connection.manage"

export type Connection = Schemas["ConnectionOut"]
export type Mapping = Schemas["AccountMapping"]
export type LedgerAccount = Schemas["AccountOut"]
export type SyncRecord = Schemas["SyncRecordOut"]
export type RecordFilter = "all" | "failed" | "skipped"

const KEY = ["accounting", "quickbooks"] as const
let watchUntil = 0
/** Keep refreshing the status for a while after asking for a sync, until the worker picks it up and finishes. */
export function watchForSync(ms = 120_000) { watchUntil = Date.now() + ms }

export function useConnection() {
  const { permissions } = useSession()
  return useQuery({
    queryKey: KEY, enabled: permissions.has(SYNC_READ),
    queryFn: async () => unwrap(await api.GET("/api/v1/accounting/quickbooks")),
    // While a sync runs, or shortly after one was asked for (the worker may not have started it yet), refresh so the
    // counts and the log catch up.
    refetchInterval: (query) => (query.state.data && (isRunning(query.state.data) || Date.now() < watchUntil) ? 5000 : false),
  })
}

export function isRunning(c: Connection): boolean {
  return !!c.last_sync_started_at && (!c.last_sync_finished_at || c.last_sync_finished_at < c.last_sync_started_at)
}

export function useAccounts(enabled: boolean) {
  return useQuery({ queryKey: [...KEY, "accounts"], enabled, staleTime: 60_000, queryFn: async () => unwrap(await api.GET("/api/v1/accounting/quickbooks/accounts")) })
}

export function useRecords(filter: RecordFilter, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: [...KEY, "records", filter], enabled, initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => unwrap(await api.GET("/api/v1/accounting/quickbooks/records", { params: { query: { limit: 50, cursor: pageParam, status: filter === "all" ? undefined : [filter] } } })),
    getNextPageParam: (p) => p.next_cursor ?? undefined,
  })
}

function useAccountingMutation<T, R>(fn: (data: T) => Promise<R>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: async () => { await qc.invalidateQueries({ queryKey: KEY }) } })
}

export function useStartConnect() {
  return useMutation({ mutationFn: async () => unwrap(await api.POST("/api/v1/accounting/quickbooks/connect")) })
}
export function useSaveSettings() {
  return useAccountingMutation(async (body: Schemas["SettingsIn"]) => unwrap(await api.PUT("/api/v1/accounting/quickbooks/settings", { body })))
}
export function useDisconnect() {
  return useAccountingMutation(async (version: number) => unwrap(await api.POST("/api/v1/accounting/quickbooks/disconnect", { body: { version } })))
}
export function useSyncNow() {
  return useAccountingMutation(async () => { watchForSync(); return unwrap(await api.POST("/api/v1/accounting/quickbooks/sync")) })
}
export function useRetryRecord() {
  return useAccountingMutation(async (id: string) => {
    watchForSync()
    return unwrap(await api.POST("/api/v1/accounting/quickbooks/records/{record_id}/retry", { params: { path: { record_id: id } } }))
  })
}
