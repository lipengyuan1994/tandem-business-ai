import { useTranslation } from "react-i18next"
import { EmptyState } from "@/components/common/EmptyState"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { Skeleton } from "@/components/ui/skeleton"
import { CustomerCareSettings } from "./CustomerCareSettings"
import { AgentCard } from "./AgentCard"
import { TeamActivity } from "./TeamActivity"
import { useAgents } from "./team-api"
import { teamDetailRoute } from "./routes"

export function TeamPage() {
  const { t } = useTranslation()
  return <PermissionGate permission="approvals.proposal.read" fallback={<EmptyState title={t("ai.activity.noAccess")} />}><TeamList /></PermissionGate>
}
function TeamList() {
  const { t } = useTranslation()
  const agents = useAgents()
  return <div className="space-y-6"><PageHeader title={t("ai.team.title")} description={t("ai.team.description")} />
    {agents.error ? <QueryError error={agents.error} onRetry={() => agents.refetch()} /> : agents.isPending ? <Skeleton className="h-64 w-full" /> : !agents.data?.items.length ? <EmptyState title={t("ai.team.empty")} /> : <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">{agents.data.items.map((agent) => <AgentCard key={agent.key} agent={agent} />)}</div>}
  </div>
}
export function AgentDetailPage() {
  const { key } = teamDetailRoute.useParams()
  const { t } = useTranslation()
  return <PermissionGate permission="approvals.proposal.read" fallback={<EmptyState title={t("ai.activity.noAccess")} />}><AgentDetail agentKey={key} /></PermissionGate>
}
function AgentDetail({ agentKey }: { agentKey: string }) {
  const { t } = useTranslation()
  const agents = useAgents()
  if (agents.error) return <QueryError error={agents.error} onRetry={() => agents.refetch()} />
  if (agents.isPending) return <Skeleton className="h-64 w-full" />
  const agent = agents.data?.items.find((item) => item.key === agentKey)
  if (!agent) return <EmptyState title={t("ai.team.notFound")} />
  return <div className="max-w-4xl space-y-6"><a className="record-link" href="/ai/team">{t("ai.team.back")}</a><PageHeader title={agent.name} description={agent.job} /><AgentCard agent={agent} />{agent.key === "customer_care" ? <PermissionGate permission="org.settings.write"><CustomerCareSettings /></PermissionGate> : null}<TeamActivity agentKey={agent.key} /></div>
}
