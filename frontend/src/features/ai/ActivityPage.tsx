import type { ColumnDef } from "@tanstack/react-table"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { DataTable } from "@/components/common/DataTable"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { QueryError } from "@/components/common/QueryError"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { ProposalDelivery } from "./DeliveryStatus"
import { useAgents } from "./team-api"
import { teamReason } from "./team-text"
import { TeamActivity } from "./TeamActivity"
import { activityRoute } from "./routes"
import { Label } from "@/components/ui/label"
import { useAutonomyActivity, type ActivityEntry } from "./autonomy-api"

export function ActivityPage() {
  const { t } = useTranslation()
  return <PermissionGate permission="approvals.proposal.read" fallback={<EmptyState title={t("ai.activity.noAccess")} />}><AgentActivity /></PermissionGate>
}

function AgentActivity() {
  const { t } = useTranslation()
  const { agent = "" } = activityRoute.useSearch()
  const navigate = activityRoute.useNavigate()
  const agents = useAgents()
  return <div className="space-y-6">
    <PageHeader title={t("ai.activity.title")} description={t("ai.activity.description")} />
    <div className="space-y-2"><Label htmlFor="activity-agent">{t("ai.team.agentFilter")}</Label><select id="activity-agent" className="h-10 rounded-md border bg-background px-3" value={agent} onChange={(e) => void navigate({ search: { agent: e.target.value || undefined } })}>
      <option value="">{t("ai.team.allAgents")}</option>{agents.data?.items.map((item) => <option key={item.key} value={item.key}>{item.name}</option>)}
    </select></div>
    {agents.error ? <QueryError error={agents.error} onRetry={() => agents.refetch()} /> : null}
    {agent ? <TeamActivity key={agent} agentKey={agent} /> : <ActivityList />}
  </div>
}

const TONES = { acted: "success", would_act: "neutral", fell_back: "pending", blocked: "failed" } as const

function ActivityList() {
  const { t } = useTranslation()
  const { businessTimeZone, permissions } = useSession()
  const activity = useAutonomyActivity()
  const agents = useAgents()
  const columns: ColumnDef<ActivityEntry, unknown>[] = [
    { header: t("ai.activity.when"), cell: ({ row }) => <LocalTime iso={row.original.created_at} timeZone={businessTimeZone} pattern="d MMM yyyy HH:mm" /> },
    { header: t("ai.activity.action"), cell: ({ row: { original: entry } }) => <div className="space-y-1"><a className="record-link" href={`/approvals/${entry.proposal_id}`}>{entry.title}</a>{"agent" in entry && typeof entry.agent === "string" ? <p>{agents.data?.items.find((a) => a.key === entry.agent)?.name ?? t(`ai.team.names.${entry.agent}`, { defaultValue: entry.agent.replaceAll("_", " ") })}</p> : null}<p className="text-sm">{t(`ai.autonomy.actions.${entry.action_type.replaceAll(".", "_")}`, { defaultValue: entry.action_type })}</p></div> },
    { header: t("ai.activity.outcome"), cell: ({ row: { original: entry } }) => <div className="space-y-2"><span className="status-badge" data-tone={TONES[entry.outcome]}>{t(`ai.activity.outcomes.${entry.outcome}`)}</span>{entry.reasons.map((reason) => <p key={reason} className="text-sm">{teamReason(t, reason)}</p>)}</div> },
    { header: t("ai.activity.result"), cell: ({ row: { original: entry } }) => <div className="space-y-2"><StatusBadge status={entry.status} /><ProposalDelivery proposalId={entry.proposal_id} state={entry.delivery_state} />
      {entry.failure_code ? <p className="text-sm">{t(`ai.activity.failures.${entry.failure_code.replaceAll(".", "_")}`, { defaultValue: t("ai.activity.failedHint") })}</p> : null}
      {entry.status === "succeeded" && entry.result_link && permissions.has("inventory.reorder.read") ? <a className="record-link" href={entry.result_link}>{t("ai.activity.draftCreated")}</a> : null}
    </div> },
    { header: t("ai.activity.rule"), cell: ({ row: { original: entry } }) => <div className="space-y-1"><p>{t("ai.activity.ruleVersion", { id: entry.rule_id, version: entry.rule_version })}</p><p className="text-sm">{t("ai.activity.grantedBy", { who: entry.granted_by_name || entry.granted_by || t("ai.activity.unknownGrantor") })}</p></div> },
  ]
  return <div className="space-y-6">
    <DataTable columns={columns} data={activity.data?.pages.flatMap((p) => p.items)} isLoading={activity.isPending} error={activity.error} onRetry={() => activity.refetch()} caption={t("ai.activity.title")} emptyTitle={t("ai.activity.empty")} emptyDescription={t("ai.activity.emptyHelp")} hasMore={activity.hasNextPage} onLoadMore={() => void activity.fetchNextPage()} />
  </div>
}
