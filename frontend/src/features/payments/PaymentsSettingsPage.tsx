// /settings/payments: set up the business's Stripe account so customers can pay invoices online (ADR-022, phase 1).
import { useNavigate, useRouterState } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { MoneyText } from "@/components/common/MoneyText"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useBusiness } from "@/features/org/hooks"
import { toastError } from "@/lib/errors"
import { formatMoney } from "@/lib/money"
import {
  CONNECTION_MANAGE, CONNECTION_READ, useDisconnectStripe, usePaymentConnection, usePayouts, useRefreshAccount, useStartOnboarding,
  type PaymentConnection, type Payout,
} from "./api"

// Countries where Stripe offers accounts to US-platform connected businesses that Tandem expects first; others can follow.
const COUNTRIES = ["US", "CA", "GB", "IE", "AU", "NZ"] as const
const STATUS_TONE = { not_connected: "neutral", onboarding: "pending", active: "success", restricted: "failed", disconnected: "neutral" } as const

export function PaymentsSettingsPage() {
  const { t } = useTranslation()
  return (
    <PermissionGate permission={CONNECTION_READ} fallback={<EmptyState title={t("payments.noAccess")} />}>
      <div className="max-w-3xl space-y-8">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">{t("payments.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("payments.intro")}</p>
        </div>
        <Body />
      </div>
    </PermissionGate>
  )
}

/** Stripe sends the browser back with ?stripe=return (finished or left onboarding) or ?stripe=refresh (the link expired). */
function useStripeReturn(): "return" | "refresh" | null {
  const navigate = useNavigate()
  const searchStr = useRouterState({ select: (s) => s.location.searchStr })
  const [outcome] = useState<"return" | "refresh" | null>(() => {
    const value = new URLSearchParams(searchStr).get("stripe")
    return value === "return" || value === "refresh" ? value : null
  })
  useEffect(() => {
    if (outcome) void navigate({ to: "/settings/payments", search: {}, replace: true })
  }, [outcome, navigate])
  return outcome
}

function Body() {
  const { t } = useTranslation()
  const connection = usePaymentConnection()
  const outcome = useStripeReturn()
  const refresh = useRefreshAccount()
  const checked = useRef(false)
  const loaded = !!connection.data
  const hasAccount = !!connection.data?.stripe_account_id
  // Coming back from Stripe: read the account straight away rather than wait for Stripe's event.
  useEffect(() => {
    if (outcome !== "return" || checked.current || !loaded || !hasAccount) return
    checked.current = true
    refresh.mutate(undefined, { onError: (e) => toastError(e) })
  }, [outcome, loaded, hasAccount, refresh])
  if (connection.error) return <QueryError error={connection.error} onRetry={() => connection.refetch()} />
  if (connection.isPending || !connection.data) return <Skeleton className="h-48 w-full" />
  const c = connection.data
  return (
    <>
      {outcome === "refresh" && <p role="status" className="rounded-md border p-3 text-sm">{t("payments.linkExpired")}</p>}
      {!c.configured && <p className="rounded-md border p-3 text-sm">{t("payments.notConfigured")}</p>}
      <StatusCard c={c} />
      <p className="text-xs text-muted-foreground">{t("payments.cardsNote")}</p>
      {c.stripe_account_id ? <Payouts /> : null}
    </>
  )
}

function StatusCard({ c }: { c: PaymentConnection }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const zone = useBusiness().data?.timezone ?? "UTC"
  const start = useStartOnboarding()
  const refresh = useRefreshAccount()
  const disconnect = useDisconnectStripe()
  const [country, setCountry] = useState(c.country ?? "US")
  const [confirming, setConfirming] = useState(false)
  const [conflict, setConflict] = useState(false)
  const canManage = permissions.has(CONNECTION_MANAGE)
  const hasAccount = !!c.stripe_account_id && c.status !== "disconnected"
  const due = c.requirements_due ?? []

  const goToStripe = () => start.mutate(country, {
    onSuccess: (out) => { window.location.assign(out.url) },
    onError: (e) => toastError(e),
  })
  const startLabel = c.status === "not_connected" ? "payments.setUp"
    : c.status === "disconnected" ? "payments.reconnect"
    : "payments.continue"
  return (
    <section aria-labelledby="stripe-status" className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="stripe-status" className="font-medium">{t("payments.stripe")}</h3>
        <span data-tone={STATUS_TONE[c.status]} className="status-badge">{t(`payments.status.${c.status}`)}</span>
        {c.mode === "test" && <span className="text-xs text-muted-foreground">{t("payments.testMode")}</span>}
      </div>
      <p className="text-sm">{t(`payments.explain.${c.status}`)}</p>
      {hasAccount && (
        <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
          <Flag label={t("payments.charges")} on={!!c.charges_enabled} />
          <Flag label={t("payments.payouts")} on={!!c.payouts_enabled} />
          <Flag label={t("payments.details")} on={!!c.details_submitted} />
        </dl>
      )}
      {hasAccount && due.length > 0 && (
        <div className="text-sm">
          <p>{t("payments.stillNeeded", { count: due.length })}</p>
          <ul className="list-disc ps-5 text-muted-foreground">
            {due.slice(0, 8).map((r) => <li key={r}>{t(`payments.requirement.${r.split(".")[0]}`, { defaultValue: r })}</li>)}
          </ul>
        </div>
      )}
      {hasAccount && c.default_currency && c.default_currency.toUpperCase() !== c.business_currency && (
        <p className="text-sm text-destructive">{t("payments.currencyMismatch", { business: c.business_currency, stripe: c.default_currency.toUpperCase() })}</p>
      )}
      {c.last_error && <p className="text-sm text-destructive">{c.last_error}</p>}
      {c.last_checked_at && <p className="text-xs text-muted-foreground">{t("payments.lastChecked")} <LocalTime iso={c.last_checked_at} timeZone={zone} /></p>}
      {canManage && c.configured && c.status !== "active" && !c.stripe_account_id && (
        <div className="max-w-xs space-y-1">
          <Label htmlFor="stripe-country">{t("payments.country")}</Label>
          <select id="stripe-country" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={country} onChange={(e) => setCountry(e.target.value)}>
            {COUNTRIES.map((code) => <option key={code} value={code}>{t(`payments.countries.${code}`)}</option>)}
          </select>
          <p className="text-xs text-muted-foreground">{t("payments.countryHelp")}</p>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {canManage && c.configured && c.status !== "active" && (
          <Button onClick={goToStripe} disabled={start.isPending}>{t(startLabel)}</Button>
        )}
        {hasAccount && (
          <Button variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate(undefined, { onError: (e) => toastError(e) })}>{t("payments.checkNow")}</Button>
        )}
        {hasAccount && c.dashboard_url && (
          <Button variant="outline" asChild><a href={c.dashboard_url} target="_blank" rel="noreferrer noopener">{t("payments.openDashboard")}</a></Button>
        )}
        {canManage && hasAccount && (
          <Button variant="ghost" onClick={() => setConfirming(true)}>{t("payments.disconnect")}</Button>
        )}
      </div>
      <ConfirmDialog open={confirming} onOpenChange={setConfirming} destructive pending={disconnect.isPending}
        title={t("payments.disconnectTitle")} description={t("payments.disconnectBody")} confirmLabel={t("payments.disconnect")}
        onConfirm={() => disconnect.mutate(c.version ?? 0, {
          onSuccess: () => setConfirming(false),
          onError: (e) => { setConfirming(false); toastError(e, () => setConflict(true)) },
        })} />
      <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => setConflict(false)} />
    </section>
  )
}

function Flag({ label, on }: { label: string; on: boolean }) {
  const { t } = useTranslation()
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={on ? "font-medium text-emerald-700 dark:text-emerald-400" : "font-medium"}>{t(on ? "payments.yes" : "payments.notYet")}</dd>
    </div>
  )
}

function Payouts() {
  const { t } = useTranslation()
  const payouts = usePayouts(true)
  if (payouts.error) return <QueryError error={payouts.error} onRetry={() => payouts.refetch()} />
  if (payouts.isPending || !payouts.data) return <Skeleton className="h-24 w-full" />
  const money = (p: Payout, amount: number) => formatMoney({ amount_minor: amount, currency: p.currency })
  return (
    <section className="space-y-2" aria-labelledby="payouts-title">
      <h3 id="payouts-title" className="text-base font-semibold">{t("payments.payoutsList.title")}</h3>
      <p className="text-sm text-muted-foreground">{t("payments.payoutsList.help")}</p>
      {payouts.data.length === 0 ? <p className="text-sm">{t("payments.payoutsList.empty")}</p> : (
        <Table>
          <TableHeader><TableRow>
            <TableHead>{t("payments.payoutsList.arrives")}</TableHead>
            <TableHead>{t("payments.payoutsList.amount")}</TableHead>
            <TableHead>{t("payments.payoutsList.state")}</TableHead>
            <TableHead>{t("payments.payoutsList.contents")}</TableHead>
          </TableRow></TableHeader>
          <TableBody>{payouts.data.map((p) => <TableRow key={p.id}>
            <TableCell className="tabular-nums">{p.arrival_date ?? "—"}</TableCell>
            <TableCell><MoneyText value={{ amount_minor: p.amount_minor, currency: p.currency }} /></TableCell>
            <TableCell className="whitespace-normal">
              {t(`payments.payoutsList.states.${p.status}`)}
              {p.status === "failed" ? <p role="note" className="mt-1 text-sm">{t("payments.payoutsList.failed")}</p> : null}
            </TableCell>
            <TableCell className="whitespace-normal text-sm">
              {!p.automatic ? t("payments.payoutsList.manual")
                : p.summarized ? t("payments.payoutsList.summary", { charges: money(p, p.charges_minor), fees: money(p, p.fees_minor), count: p.transaction_count })
                : t("payments.payoutsList.notYet")}
            </TableCell>
          </TableRow>)}</TableBody>
        </Table>
      )}
    </section>
  )
}
