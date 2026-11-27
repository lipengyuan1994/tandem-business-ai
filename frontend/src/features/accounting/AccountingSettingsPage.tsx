// /settings/accounting: connect QuickBooks Online, map its accounts, and watch what was sent and what failed.
import { useNavigate, useRouterState } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { useSession } from "@/app/session-context"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { useBusiness } from "@/features/org/hooks"
import { toastError } from "@/lib/errors"
import {
  CONNECTION_MANAGE, SYNC_READ, SYNC_RUN, isRunning, useAccounts, useConnection, useDisconnect, useRecords, useRetryRecord,
  useSaveSettings, useStartConnect, useSyncNow, type Connection, type LedgerAccount, type Mapping, type RecordFilter,
} from "./api"

const MAPPING: { field: keyof Mapping; types: string[]; required?: boolean }[] = [
  { field: "income_account_id", types: ["Income", "Other Income"], required: true },
  { field: "deposit_account_id", types: ["Bank", "Other Current Asset"] },
  { field: "receivable_account_id", types: ["Accounts Receivable"] },
  { field: "tax_account_id", types: ["Other Current Liability"] },
  { field: "expense_account_id", types: ["Expense", "Cost of Goods Sold", "Other Expense"], required: true },
  { field: "payable_account_id", types: ["Accounts Payable"] },
  { field: "bank_account_id", types: ["Bank"], required: true },
  { field: "stripe_clearing_account_id", types: ["Bank", "Other Current Asset"] },
  { field: "stripe_fees_account_id", types: ["Expense", "Other Expense"] },
]
const STATUS_TONE = { connected: "success", pending: "pending", needs_reconnect: "failed", disconnected: "neutral", not_connected: "neutral" } as const

export function AccountingSettingsPage() {
  const { t } = useTranslation()
  return (
    <PermissionGate permission={SYNC_READ} fallback={<EmptyState title={t("accounting.noAccess")} />}>
      <div className="max-w-3xl space-y-8">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">{t("accounting.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("accounting.intro")}</p>
        </div>
        <ConnectResult />
        <Body />
      </div>
    </PermissionGate>
  )
}

/** Intuit sends the browser back with ?quickbooks=connected or ?quickbooks=error&reason=…; say so once. */
function ConnectResult() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const searchStr = useRouterState({ select: (s) => s.location.searchStr })
  const [result] = useState(() => {
    const params = new URLSearchParams(searchStr)
    return { outcome: params.get("quickbooks"), reason: params.get("reason") }
  })
  useEffect(() => {
    if (result.outcome) void navigate({ to: "/settings/accounting", search: {}, replace: true })
  }, [result.outcome, navigate])
  if (result.outcome === "connected") return <p role="status" className="rounded-md border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100">{t("accounting.connectedNow")}</p>
  if (result.outcome !== "error") return null
  const reason = result.reason && /^accounting\.[a-z_]+$/.test(result.reason) ? result.reason : ""
  return (
    <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
      {t("accounting.connectFailed")} {t(`errors.${reason}`, { defaultValue: t("accounting.connectFailedGeneric") })}
    </p>
  )
}

function Body() {
  const { t } = useTranslation()
  const connection = useConnection()
  if (connection.error) return <QueryError error={connection.error} onRetry={() => connection.refetch()} />
  if (connection.isPending || !connection.data) return <Skeleton className="h-48 w-full" />
  const c = connection.data
  return (
    <>
      {!c.configured && <p className="rounded-md border p-3 text-sm">{t("accounting.notConfigured")}</p>}
      <StatusCard c={c} />
      {(c.status === "connected" || c.status === "needs_reconnect") && (
        <>
          <PermissionGate permission={CONNECTION_MANAGE} fallback={<MappingSummary c={c} />}>
            <SettingsForm key={c.version ?? 0} c={c} onReload={() => void connection.refetch()} />
          </PermissionGate>
          <SyncLog />
        </>
      )}
      {c.status === "disconnected" && <SyncLog />}
    </>
  )
}

function StatusCard({ c }: { c: Connection }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const zone = useBusiness().data?.timezone ?? "UTC"
  const start = useStartConnect()
  const syncNow = useSyncNow()
  const disconnect = useDisconnect()
  const [confirming, setConfirming] = useState(false)
  const canManage = permissions.has(CONNECTION_MANAGE)
  const connected = c.status === "connected"
  const running = isRunning(c)

  const beginConnect = () => start.mutate(undefined, {
    onSuccess: (out) => { window.location.assign(out.authorize_url) },
    onError: (e) => toastError(e),
  })
  return (
    <section aria-labelledby="qb-status" className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="qb-status" className="font-medium">{t("accounting.quickbooks")}</h3>
        <span data-tone={STATUS_TONE[c.status]} className="status-badge">{t(`accounting.status.${c.status}`)}</span>
        {c.environment === "sandbox" && <span className="text-xs text-muted-foreground">{t("accounting.sandbox")}</span>}
      </div>
      {c.company_name && <p className="text-sm">{t("accounting.company", { name: c.company_name, currency: c.home_currency ?? "?" })}</p>}
      {c.status === "needs_reconnect" && <p className="text-sm text-destructive">{t("accounting.needsReconnect")}</p>}
      {c.home_currency && c.home_currency !== c.business_currency && (
        <p className="text-sm text-destructive">{t("accounting.currencyMismatch", { business: c.business_currency, books: c.home_currency })}</p>
      )}
      {connected && (c.missing_mapping ?? []).length > 0 && <p className="text-sm text-destructive">{t("accounting.mappingNeeded")}</p>}
      {(connected || c.status === "disconnected") && (
        <dl className="grid grid-cols-3 gap-2 text-sm">
          {(["synced", "failed", "skipped"] as const).map((k) => (
            <div key={k}><dt className="text-muted-foreground">{t(`accounting.counts.${k}`)}</dt><dd className="text-lg font-semibold tabular-nums">{c.counts?.[k] ?? 0}</dd></div>
          ))}
        </dl>
      )}
      {running ? <p className="text-sm" role="status">{t("accounting.running")}</p>
        : c.last_sync_finished_at && <p className="text-sm text-muted-foreground">{t("accounting.lastSync")} <LocalTime iso={c.last_sync_finished_at} timeZone={zone} /></p>}
      {c.last_sync_error && <p className="text-sm text-destructive">{c.last_sync_error}</p>}
      <div className="flex flex-wrap gap-2">
        {canManage && c.configured && !connected && (
          <Button onClick={beginConnect} disabled={start.isPending}>{t(c.status === "needs_reconnect" ? "accounting.reconnect" : "accounting.connect")}</Button>
        )}
        {connected && permissions.has(SYNC_RUN) && (
          <Button variant="outline" disabled={syncNow.isPending || running || (c.missing_mapping ?? []).length > 0} onClick={() => syncNow.mutate(undefined, {
            onSuccess: (out) => toast.success(t(out.queued ? "accounting.syncQueued" : "accounting.syncWaiting")), onError: (e) => toastError(e),
          })}>{t("accounting.syncNow")}</Button>
        )}
        {canManage && (connected || c.status === "needs_reconnect") && (
          <Button variant="ghost" onClick={() => setConfirming(true)}>{t("accounting.disconnect")}</Button>
        )}
      </div>
      <ConfirmDialog open={confirming} onOpenChange={setConfirming} destructive pending={disconnect.isPending}
        title={t("accounting.disconnectTitle")} description={t("accounting.disconnectBody")} confirmLabel={t("accounting.disconnect")}
        onConfirm={() => disconnect.mutate(c.version ?? 0, { onSuccess: () => setConfirming(false), onError: (e) => toastError(e) })} />
    </section>
  )
}

function MappingSummary({ c }: { c: Connection }) {
  const { t } = useTranslation()
  return (
    <p className="text-sm text-muted-foreground">
      {(c.missing_mapping ?? []).length ? t("accounting.mappingByOwner") : t("accounting.syncingFrom", { date: c.sync_from })}
    </p>
  )
}

function SettingsForm({ c, onReload }: { c: Connection; onReload: () => void }) {
  const { t } = useTranslation()
  const accounts = useAccounts(c.status === "connected")
  const save = useSaveSettings()
  const [mapping, setMapping] = useState<Mapping>(c.mapping ?? {})
  const [syncFrom, setSyncFrom] = useState(c.sync_from ?? "")
  const [autoSync, setAutoSync] = useState(c.auto_sync ?? true)
  const [conflict, setConflict] = useState(false)
  const missing = MAPPING.filter((m) => m.required && !mapping[m.field]).map((m) => m.field)

  return (
    <form aria-labelledby="qb-settings" className="space-y-4" onSubmit={(e) => {
      e.preventDefault()
      if (!syncFrom) return
      save.mutate({ ...mapping, version: c.version ?? 0, sync_from: syncFrom, auto_sync: autoSync }, {
        onSuccess: () => toast.success(t("accounting.saved")), onError: (err) => toastError(err, () => setConflict(true)),
      })
    }}>
      <h3 id="qb-settings" className="font-medium">{t("accounting.mappingTitle")}</h3>
      <p className="text-sm text-muted-foreground">{t("accounting.mappingHelp")}</p>
      {accounts.error && <QueryError error={accounts.error} onRetry={() => accounts.refetch()} />}
      <div className="grid gap-4 sm:grid-cols-2">
        {MAPPING.map(({ field, types, required }) => (
          <AccountSelect key={field} field={field} required={required} value={mapping[field] ?? ""}
            options={(accounts.data ?? []).filter((a) => types.includes(a.account_type))} loading={accounts.isPending && c.status === "connected"}
            onChange={(value) => setMapping((m) => ({ ...m, [field]: value || null }))} />
        ))}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="qb-sync-from">{t("accounting.syncFrom")}</Label>
          <Input id="qb-sync-from" type="date" required value={syncFrom} onChange={(e) => setSyncFrom(e.target.value)} />
          <p className="text-xs text-muted-foreground">{t("accounting.syncFromHelp")}</p>
        </div>
        <div className="flex items-center gap-2 self-center">
          <Switch id="qb-auto" checked={autoSync} onCheckedChange={setAutoSync} />
          <Label htmlFor="qb-auto">{t("accounting.autoSync")}</Label>
        </div>
      </div>
      {missing.length > 0 && <p className="text-sm text-muted-foreground">{t("accounting.mappingNeeded")}</p>}
      <Button type="submit" disabled={save.isPending || !syncFrom}>{t("accounting.save")}</Button>
      <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); onReload() }} />
    </form>
  )
}

function AccountSelect({ field, value, options, loading, required, onChange }: {
  field: keyof Mapping; value: string; options: LedgerAccount[]; loading: boolean; required?: boolean; onChange: (value: string) => void
}) {
  const { t } = useTranslation()
  const id = `qb-${field}`
  // Keep a saved account selectable even if QuickBooks no longer lists it (made inactive there).
  const known = !value || options.some((a) => a.id === value)
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{t(`accounting.mapping.${field}`)}{required ? " *" : ""}</Label>
      <select id={id} className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={value} disabled={loading} onChange={(e) => onChange(e.target.value)}>
        <option value="">{required ? t("accounting.chooseAccount") : t("accounting.quickbooksDefault")}</option>
        {!known && <option value={value}>{t("accounting.unknownAccount", { id: value })}</option>}
        {options.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select>
    </div>
  )
}

function SyncLog() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const zone = useBusiness().data?.timezone ?? "UTC"
  const [filter, setFilter] = useState<RecordFilter>("all")
  const records = useRecords(filter, true)
  const finished = useConnection().data?.last_sync_finished_at
  const { refetch } = records
  const seen = useRef(finished)
  // A finished run changes the log: reload it when the status shows a new finish time.
  useEffect(() => {
    if (finished === seen.current) return
    seen.current = finished
    void refetch()
  }, [finished, refetch])
  const retry = useRetryRecord()
  const rows = records.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <section aria-labelledby="qb-log" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="qb-log" className="font-medium">{t("accounting.logTitle")}</h3>
        <div role="group" aria-label={t("accounting.logFilter")} className="flex gap-1">
          {(["all", "failed", "skipped"] as const).map((f) => (
            <Button key={f} size="sm" variant={filter === f ? "secondary" : "ghost"} aria-pressed={filter === f} onClick={() => setFilter(f)}>{t(`accounting.filter.${f}`)}</Button>
          ))}
        </div>
      </div>
      {records.error ? <QueryError error={records.error} onRetry={() => records.refetch()} />
        : records.isPending ? <Skeleton className="h-24 w-full" />
        : rows.length === 0 ? <p className="text-sm text-muted-foreground">{t("accounting.logEmpty")}</p>
        : (
          <ul className="divide-y rounded-md border">
            {rows.map((r) => (
              <li key={r.id} className="flex flex-wrap items-start justify-between gap-2 p-3 text-sm">
                <div className="min-w-0 space-y-0.5">
                  <p className="font-medium">{r.label}</p>
                  <p className="text-muted-foreground">
                    {t(`accounting.recordStatus.${r.status}`)}
                    {r.status === "synced" && r.remote_state && r.remote_state !== "active" ? ` · ${t(`accounting.remoteState.${r.remote_state}`, { defaultValue: r.remote_state })}` : ""}
                    {r.last_attempt_at ? " · " : ""}{r.last_attempt_at && <LocalTime iso={r.last_attempt_at} timeZone={zone} />}
                  </p>
                  {r.last_error && <p className={r.status === "failed" ? "text-destructive" : "text-muted-foreground"}>{r.last_error}</p>}
                  {r.status === "failed" && r.next_attempt_at && <p className="text-xs text-muted-foreground">{t("accounting.nextTry")} <LocalTime iso={r.next_attempt_at} timeZone={zone} /></p>}
                </div>
                {r.status === "failed" && permissions.has(SYNC_RUN) && (
                  <Button size="sm" variant="outline" disabled={retry.isPending} aria-label={t("accounting.retryLabel", { label: r.label })}
                    onClick={() => retry.mutate(r.id, { onSuccess: () => toast.success(t("accounting.retryQueued")), onError: (e) => toastError(e) })}>{t("accounting.retry")}</Button>
                )}
              </li>
            ))}
          </ul>
        )}
      {records.hasNextPage && <Button variant="outline" size="sm" disabled={records.isFetchingNextPage} onClick={() => void records.fetchNextPage()}>{t("common.loadMore")}</Button>}
    </section>
  )
}
