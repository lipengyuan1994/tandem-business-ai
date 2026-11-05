import type { ColumnDef } from "@tanstack/react-table"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { DataTable } from "@/components/common/DataTable"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { ProposalDelivery } from "./DeliveryStatus"
import { useAutonomyActivity, type ActivityEntry } from "./autonomy-api"

export function ActivityPage() {
  const { t } = useTranslation()
  return <PermissionGate permission="approvals.proposal.read" fallback={<EmptyState title={t("ai.activity.noAccess")} />}><ActivityList /></PermissionGate>
}

const TONES = { acted: "success", would_act: "neutral", fell_back: "pending", blocked: "failed" } as const

function ActivityList() {
  const { t } = useTranslation()
  const { businessTimeZone, permissions } = useSession()
  const activity = useAutonomyActivity()
  const columns: ColumnDef<ActivityEntry, unknown>[] = [
    { header: t("ai.activity.when"), cell: ({ row }) => <LocalTime iso={row.original.created_at} timeZone={businessTimeZone} pattern="d MMM yyyy HH:mm" /> },
    { header: t("ai.activity.action"), cell: ({ row: { original: entry } }) => <div className="space-y-1"><a className="record-link" href={`/approvals/${entry.proposal_id}`}>{entry.title}</a><p className="text-sm">{t(`ai.autonomy.actions.${entry.action_type.replaceAll(".", "_")}`, { defaultValue: entry.action_type })}</p></div> },
    { header: t("ai.activity.outcome"), cell: ({ row: { original: entry } }) => <div className="space-y-2"><span className="status-badge" data-tone={TONES[entry.outcome]}>{t(`ai.activity.outcomes.${entry.outcome}`)}</span>{entry.reasons.map((reason) => <p key={reason} className="text-sm">{t(`ai.activity.reasons.${reason}`, { defaultValue: t("ai.activity.unknownReason") })}</p>)}</div> },
    { header: t("ai.activity.result"), cell: ({ row: { original: entry } }) => <div className="space-y-2"><StatusBadge status={entry.status} /><ProposalDelivery proposalId={entry.proposal_id} state={entry.delivery_state} />
      {entry.failure_code ? <p className="text-sm">{t(`ai.activity.failures.${entry.failure_code.replaceAll(".", "_")}`, { defaultValue: t("ai.activity.failedHint") })}</p> : null}
      {entry.status === "succeeded" && entry.result_link && permissions.has("inventory.reorder.read") ? <a className="record-link" href={entry.result_link}>{t("ai.activity.draftCreated")}</a> : null}
    </div> },
    { header: t("ai.activity.rule"), cell: ({ row: { original: entry } }) => <div className="space-y-1"><p>{t("ai.activity.ruleVersion", { id: entry.rule_id, version: entry.rule_version })}</p><p className="text-sm">{t("ai.activity.grantedBy", { who: entry.granted_by_name || entry.granted_by || t("ai.activity.unknownGrantor") })}</p></div> },
  ]
  return <div className="space-y-6"><PageHeader title={t("ai.activity.title")} description={t("ai.activity.description")} />
    <DataTable columns={columns} data={activity.data?.pages.flatMap((p) => p.items)} isLoading={activity.isPending} error={activity.error} onRetry={() => activity.refetch()} caption={t("ai.activity.title")} emptyTitle={t("ai.activity.empty")} emptyDescription={t("ai.activity.emptyHelp")} hasMore={activity.hasNextPage} onLoadMore={() => void activity.fetchNextPage()} />
  </div>
}
