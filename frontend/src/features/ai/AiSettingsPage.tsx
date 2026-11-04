// /settings/ai: where AI may process text, the daily limit, what AI used, and a history of runs (never any text).
import type { ColumnDef } from "@tanstack/react-table"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { BusinessSettings } from "@/api/types"
import { useSession } from "@/app/session-context"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { DataTable } from "@/components/common/DataTable"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { useSettings, useUpdateSettings } from "@/features/org/hooks"
import { toastError } from "@/lib/errors"
import { useAiRuns, useAiUsage } from "./api"
import { AiDisabledBanner } from "./AiDisabledBanner"
import { useAiAvailability } from "./text"
import type { AiPolicy, RunOut, UsageDay } from "./types"

const CHOICES = ["hosted_vultr", "hosted_any", "strict_private"] as const
const RUN_TONES = { running: "approved", succeeded: "success", failed: "failed", step_limit: "pending", budget_exceeded: "pending", policy_blocked: "neutral" } as const
const number = new Intl.NumberFormat("en-US")

export function AiSettingsPage() {
  const { t } = useTranslation()
  return (
    <PermissionGate permission="ai.settings.read" fallback={<EmptyState title={t("ai.settings.noAccess")} />}>
      <div className="max-w-3xl space-y-8">
        <h2 className="text-lg font-semibold">{t("ai.settings.title")}</h2>
        <AiDisabledBanner />
        <PolicyAndBudget />
        <UsageChart />
        <RunHistory />
      </div>
    </PermissionGate>
  )
}

function PolicyAndBudget() {
  const settings = useSettings()
  if (settings.error) return <QueryError error={settings.error} onRetry={() => settings.refetch()} />
  if (settings.isPending || !settings.data) return <Skeleton className="h-48 w-full" />
  // Keyed by version: a reload (or someone else's save) starts a fresh draft.
  return <PolicyForm key={settings.data.version} initial={settings.data.settings} version={settings.data.version} onReload={() => void settings.refetch()} />
}

function PolicyForm({ initial, version, onReload }: { initial: BusinessSettings; version: number; onReload: () => void }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const { status } = useAiAvailability()
  const usage = useAiUsage()
  const update = useUpdateSettings()
  const [policy, setPolicy] = useState<BusinessSettings["ai_policy"]>(initial.ai_policy)
  const [budget, setBudget] = useState(initial.ai_daily_token_budget == null ? "" : String(initial.ai_daily_token_budget))
  const [conflict, setConflict] = useState(false)
  const canEdit = permissions.has("ai.settings.write") && permissions.has("org.settings.write")
  const effective: AiPolicy = status?.policy ?? initial.ai_policy
  const locked = effective === "regulated"
  const budgetValue = budget.trim() === "" ? null : /^\d{1,12}$/.test(budget.trim()) ? Number(budget.trim()) : undefined
  const today = usage.data?.today

  return (
    <form className="space-y-6" onSubmit={(e) => {
      e.preventDefault()
      if (budgetValue === undefined || !canEdit || locked) return
      update.mutate({ version, settings: { ...initial, ai_policy: policy, ai_daily_token_budget: budgetValue } },
        { onSuccess: () => toast.success(t("common.saved")), onError: (e2) => toastError(e2, () => setConflict(true)) })
    }}>
      <fieldset className="space-y-3" disabled={!canEdit || locked}>
        <legend className="font-medium">{t("ai.settings.policy")}</legend>
        <p className="text-sm">{t("ai.settings.effective", { policy: t(`ai.settings.policies.${effective}.label`) })}</p>
        {locked ? <p className="rounded-md border p-3 text-sm">{t("ai.settings.policyLocked")}</p> : null}
        {(locked ? ([...CHOICES, "regulated"] as const) : CHOICES).map((p) => (
          <div key={p} className="flex items-start gap-2">
            <input id={`ai-policy-${p}`} type="radio" name="ai_policy" value={p} className="mt-1 size-4" disabled={p === "regulated"}
              checked={locked ? p === "regulated" : policy === p} onChange={() => setPolicy(p as BusinessSettings["ai_policy"])} />
            <div>
              <Label htmlFor={`ai-policy-${p}`}>{t(`ai.settings.policies.${p}.label`)}</Label>
              <p className="text-sm text-muted-foreground">{t(`ai.settings.policies.${p}.text`)}</p>
            </div>
          </div>
        ))}
      </fieldset>

      <section className="space-y-2" aria-labelledby="ai-budget-h">
        <h3 id="ai-budget-h" className="font-medium">{t("ai.settings.budget")}</h3>
        <Label htmlFor="ai-budget">{t("ai.settings.budgetLabel")}</Label>
        <Input id="ai-budget" inputMode="numeric" disabled={!canEdit} value={budget} aria-invalid={budgetValue === undefined} aria-describedby="ai-budget-help" onChange={(e) => setBudget(e.target.value)} />
        <p id="ai-budget-help" className="text-xs text-muted-foreground">{t("ai.settings.budgetHelp")}</p>
        {budgetValue === undefined ? <p role="alert" className="text-sm text-destructive">{t("ai.settings.budgetInvalid")}</p> : null}
        {today ? <p className="text-sm">{t("ai.settings.usedToday", { used: number.format(today.input_tokens + today.output_tokens), limit: number.format(today.budget_tokens), percent: today.percent })}</p> : null}
      </section>

      {canEdit ? <Button type="submit" disabled={update.isPending || budgetValue === undefined || locked}>{t("common.save")}</Button> : <p className="text-sm text-muted-foreground">{t("ai.settings.readOnly")}</p>}
      <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); onReload() }} />
    </form>
  )
}

function UsageChart() {
  const { t } = useTranslation()
  const usage = useAiUsage()
  if (usage.error) return <QueryError error={usage.error} onRetry={() => usage.refetch()} />
  if (usage.isPending) return <Skeleton className="h-32 w-full" />
  const days = [...(usage.data?.days ?? [])].sort((a, b) => a.day.localeCompare(b.day))
  const total = (d: UsageDay) => d.input_tokens + d.output_tokens
  const max = Math.max(1, ...days.map(total))
  return (
    <section aria-labelledby="ai-usage-h" className="space-y-2">
      <h3 id="ai-usage-h" className="font-medium">{t("ai.settings.usage")}</h3>
      {days.length === 0 ? <p className="text-sm text-muted-foreground">{t("ai.settings.noUsage")}</p> : (
        <ol className="flex h-32 items-end gap-0.5 rounded-lg border p-2">
          {days.map((d) => (
            <li key={d.day} className="flex h-full min-w-0 flex-1 items-end">
              <span aria-hidden="true" className="block w-full rounded-t bg-primary" style={{ height: `${Math.max(2, Math.round((total(d) / max) * 100))}%` }} />
              <span className="sr-only">{t("ai.settings.usageDay", { day: d.day, tokens: number.format(total(d)), runs: d.runs })}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 })

function RunHistory() {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const runs = useAiRuns()
  const rows = useMemo(() => runs.data?.pages.flatMap((p) => p.items), [runs.data])
  const columns = useMemo<ColumnDef<RunOut, unknown>[]>(() => [
    { header: t("ai.settings.columns.when"), cell: ({ row }) => <LocalTime iso={row.original.started_at} timeZone={businessTimeZone} pattern="d MMM HH:mm" /> },
    { header: t("ai.settings.columns.what"), cell: ({ row }) => t(`ai.settings.useCases.${row.original.use_case}`, { defaultValue: row.original.use_case }) },
    { header: t("ai.settings.columns.status"), cell: ({ row }) => (
      <span className="flex flex-wrap items-center gap-2">
        <span data-tone={RUN_TONES[row.original.status]} className="status-badge">{t(`ai.settings.runStatus.${row.original.status}`, { defaultValue: row.original.status })}</span>
        {row.original.needs_human ? <span className="text-xs">{t("ai.settings.needsHuman")}</span> : null}
      </span>
    ) },
    { header: t("ai.settings.columns.cost"), cell: ({ row }) => <span className="tabular-nums">{dollars.format(row.original.cost_micros / 1_000_000)}</span> },
    { header: t("ai.settings.columns.time"), cell: ({ row }) => <span className="tabular-nums">{t("ai.settings.seconds", { n: (row.original.latency_ms / 1000).toFixed(1) })}</span> },
  ], [t, businessTimeZone])
  return (
    <section aria-labelledby="ai-runs-h" className="space-y-2">
      <h3 id="ai-runs-h" className="font-medium">{t("ai.settings.runs")}</h3>
      <p className="text-sm text-muted-foreground">{t("ai.settings.runsHint")}</p>
      <DataTable columns={columns} data={rows} isLoading={runs.isPending} error={runs.error} onRetry={() => runs.refetch()} emptyTitle={t("ai.settings.noRuns")}
        caption={t("ai.settings.runs")} hasMore={runs.hasNextPage} onLoadMore={() => void runs.fetchNextPage()} />
    </section>
  )
}
