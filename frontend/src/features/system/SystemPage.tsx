import { QueryError } from "@/components/common/QueryError"
import type { ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { useSession } from "@/app/session-context"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { useAiUsage } from "@/features/ai/api"
import { DataPanel } from "./DataPanel"
import { EmailPanel } from "./EmailPanel"
import { BACKUP_WARN_AFTER_SECONDS, formatAge, useSystemStatus } from "./hooks"

export function SystemPage() {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const status = useSystemStatus()
  const s = status.data
  if (status.error) return <QueryError error={status.error} onRetry={() => status.refetch()} />
  if (status.isPending || !s) return <><PageHeader title={t("nav.system")} /><Skeleton className="h-64 w-full" /></>

  const backupUnknown = !s.backup.available || !["ok", "error"].includes(s.backup.result ?? "") || (s.backup.result === "ok" && s.backup.age_seconds === null)
  const backupOk = s.backup.available && s.backup.result === "ok" && (s.backup.age_seconds ?? Infinity) <= BACKUP_WARN_AFTER_SECONDS
  return (
    <>
      <PageHeader title={t("nav.system")} description={t("system.description")} />
      <div className="grid gap-4 md:grid-cols-2">
        <Panel title={t("system.version")} ok>
          <p className="text-lg font-semibold">{s.version}</p>
          <p className="text-sm text-muted-foreground">{t("system.environment", { env: s.environment })}</p>
        </Panel>
        <Panel title={t("system.backup")} ok={backupUnknown ? null : backupOk}>
          {backupUnknown ? <p>{t("system.backupUnavailable")}</p> : (
            <>
              <p className="font-medium">{s.backup.result === "ok" ? t("system.backupOk") : t("system.backupError")}</p>
              {s.backup.last_success ? <p className="text-sm">{t("system.lastBackup", { age: formatAge(s.backup.age_seconds ?? 0) })} (<LocalTime iso={s.backup.last_success} timeZone={businessTimeZone} pattern="d MMM HH:mm" />)</p> : null}
              {s.backup.error ? <p className="text-sm">{s.backup.error}</p> : null}
              {!backupOk && s.backup.result === "ok" ? <p className="text-sm">{t("system.backupLate")}</p> : null}
            </>
          )}
        </Panel>
        <Panel title={t("system.queue")} ok={s.queue.dead_jobs === 0 && (s.queue.oldest_queued_age_seconds ?? 0) < 600 && s.queue.event_deliveries_dead === 0}>
          <p>{s.queue.oldest_queued_age_seconds === null ? t("system.queueEmpty") : t("system.oldestWaiting", { age: formatAge(s.queue.oldest_queued_age_seconds) })}</p>
          <p>{t("system.deadJobs", { count: s.queue.dead_jobs })}{s.queue.dead_kinds.length ? ` (${s.queue.dead_kinds.join(", ")})` : ""}</p>
          {s.queue.event_deliveries_dead ? <p>{t("system.deadEvents", { count: s.queue.event_deliveries_dead })}</p> : null}
        </Panel>
        <Panel title={t("system.outbox")} ok={s.outbox.needs_attention === 0}>
          <p>{t("system.outboxIssues", { count: s.outbox.needs_attention })}</p>
          <p className="text-sm text-muted-foreground">{Object.entries(s.outbox.counts).map(([k, n]) => `${t(`status.${k}`, { defaultValue: k })}: ${n}`).join(" · ") || t("system.noMessages")}</p>
        </Panel>
        <AiUsagePanel />
        {s.data_profile === "regulated" ? (
          <Panel title={t("system.regulated.title")} ok>
            <p>{t("system.regulated.host", { text: s.regulated_host_baa })}</p>
            <p>{t("system.regulated.backup", { text: s.regulated_backup_baa })}</p>
            <p className="text-sm">{t("system.regulated.limit")}</p>
          </Panel>
        ) : null}
        <EmailPanel email={s.email} />
        <DataPanel />
      </div>
    </>
  )
}

/** Today's AI token use against the business budget; details live on the AI settings page. */
function AiUsagePanel() {
  const { t, i18n } = useTranslation()
  const { permissions } = useSession()
  const canRead = permissions.has("ai.settings.read")
  const usage = useAiUsage(canRead)
  const today = usage.data?.today
  const number = new Intl.NumberFormat(i18n.language)
  return (
    <Panel title={t("system.ai")} ok={today ? today.percent < 90 : true}>
      {today ? <p>{t("ai.settings.usedToday", { used: number.format(today.input_tokens + today.output_tokens), limit: number.format(today.budget_tokens), percent: today.percent })}</p> : null}
      {!canRead || usage.error ? <p>{t("system.aiUsageHidden")}</p> : null}
      {canRead ? <a className="record-link text-sm" href="/ai">{t("system.aiSettingsLink")}</a> : null}
    </Panel>
  )
}

function Panel({ title, ok, children }: { title: string; ok: boolean | null; children: ReactNode }) {
  const { t } = useTranslation()
  return (
    <Card className={cn(ok === false && "border-destructive/60")}>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-sm">{title}</CardTitle>
        <span className={cn("text-sm font-medium", ok === false ? "text-destructive" : "text-muted-foreground")}>{ok === null ? t("system.statusUnavailable") : ok ? t("system.fine") : t("system.attention")}</span>
      </CardHeader>
      <CardContent className="space-y-1">{children}</CardContent>
    </Card>
  )
}
