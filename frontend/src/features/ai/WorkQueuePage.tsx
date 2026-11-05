import type { ColumnDef } from "@tanstack/react-table"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { DataTable } from "@/components/common/DataTable"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { useWorkQueue, type WorkItem } from "./actions-api"
import { useClock } from "./useClock"
import { DeliveryBadge, ScheduledMail } from "./DeliveryStatus"

export function WorkQueuePage() { return <QueuePage exceptions={false} /> }
export function ExceptionsPage() { return <QueuePage exceptions /> }
function QueuePage({ exceptions }: { exceptions: boolean }) {
  const { t } = useTranslation()
  return <PermissionGate permission="approvals.proposal.read" fallback={<EmptyState title={t("ai.queues.noAccess")} />}><QueueList exceptions={exceptions} /></PermissionGate>
}

function Age({ since }: { since: string }) {
  const { t } = useTranslation()
  const now = useClock()
  const minutes = Math.max(0, Math.floor((now - Date.parse(since)) / 60000))
  return <span>{t("ai.queues.age", { age: minutes < 60 ? `${minutes}m` : minutes < 1440 ? `${Math.floor(minutes / 60)}h` : `${Math.floor(minutes / 1440)}d` })}</span>
}

function QueueList({ exceptions }: { exceptions: boolean }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const queue = useWorkQueue(exceptions)
  const prefix = exceptions ? "ai.exceptions" : "ai.workQueue"
  const columns: ColumnDef<WorkItem, unknown>[] = [
    { header: t("ai.queues.item"), cell: ({ row: { original: item } }) => {
      const href = item.proposal_id ? `/approvals/${item.proposal_id}` : item.quote_request_id ? `/purchasing/quote-requests/${item.quote_request_id}` : null
      return <div className="space-y-1">{href ? <a className="record-link" href={href}>{item.title}</a> : <p>{item.title}</p>}<p className="text-sm text-muted-foreground">{t(`ai.queues.kinds.${item.kind}`)}</p></div>
    } },
    { header: t("ai.queues.state"), cell: ({ row: { original: item } }) => <div className="space-y-2">
      {item.kind === "outbound_mail" ? <><DeliveryBadge state={item.status === "pending" ? "scheduled" : item.status === "cancelled" ? "stopped" : item.status === "sending" || item.status === "delivered" || item.status === "failed" || item.status === "unknown" ? item.status : "none"} />{!exceptions && item.status === "pending" ? <ScheduledMail id={item.id} sendAt={item.stop_until} /> : null}</> : <StatusBadge status={item.status} />}
      {item.kind === "proposal" && !exceptions ? <p>{t("ai.queues.waitingOn", { roles: item.waiting_on_roles?.length ? item.waiting_on_roles.map((role) => t(`roles.${role}`, { defaultValue: role.replaceAll("_", " ") })).join(", ") : t("ai.queues.approver") })}</p> : null}
      {item.failure_code ? <p>{t(`ai.cards.failures.${item.failure_code.replaceAll(".", "_")}`, { defaultValue: t(`ai.activity.failures.${item.failure_code.replaceAll(".", "_")}`, { defaultValue: t("ai.activity.failedHint") }) })}</p> : null}
      {item.reasons?.map((reason) => <p key={reason}>{t(`ai.activity.reasons.${reason}`, { defaultValue: t("ai.activity.unknownReason") })}</p>)}
    </div> },
    { header: t("ai.queues.timing"), cell: ({ row: { original: item } }) => <div className="space-y-1 text-sm">
      <LocalTime iso={item.created_at} timeZone={businessTimeZone} pattern="d MMM yyyy HH:mm" />
      <p><Age since={item.created_at} /></p>
      {item.expires_at ? <p>{t("approvals.expires")} <LocalTime iso={item.expires_at} timeZone={businessTimeZone} pattern="d MMM yyyy HH:mm" /></p> : null}
      {item.due_date ? <p>{t("ai.queues.replyBy", { date: item.due_date })}</p> : null}
    </div> },
  ]
  return <div className="space-y-6">
    <PageHeader title={t(`${prefix}.title`)} description={t(`${prefix}.description`)} />
    {exceptions && queue.data?.pages[0]?.paused ? <p role="status" className="rounded-md border border-destructive p-4">{t("ai.autonomy.paused")} <a href="/ai/autonomy" className="record-link">{t("ai.exceptions.reviewAutonomy")}</a></p> : null}
    <DataTable columns={columns} data={queue.data?.pages.flatMap((page) => page.items)} isLoading={queue.isPending} error={queue.error} onRetry={() => queue.refetch()} emptyTitle={t(`${prefix}.empty`)} emptyDescription={t(`${prefix}.emptyHelp`)} caption={t(`${prefix}.title`)} hasMore={queue.hasNextPage} onLoadMore={() => void queue.fetchNextPage()} />
  </div>
}
