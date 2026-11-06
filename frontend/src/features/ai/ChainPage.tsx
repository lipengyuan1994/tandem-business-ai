import { useTranslation } from "react-i18next"
import { EmptyState } from "@/components/common/EmptyState"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { Skeleton } from "@/components/ui/skeleton"
import { ActivitySteps } from "./TeamActivity"
import { useChain } from "./team-api"
import { teamReason, usdMicros } from "./team-text"
import { chainRoute } from "./routes"

export function ChainPage() {
  const { id } = chainRoute.useParams()
  const { t } = useTranslation()
  return <PermissionGate permission="approvals.proposal.read" fallback={<EmptyState title={t("ai.activity.noAccess")} />}><Chain id={id} /></PermissionGate>
}
function Chain({ id }: { id: string }) {
  const { t } = useTranslation()
  const chain = useChain(id)
  if (chain.error) return <QueryError error={chain.error} onRetry={() => chain.refetch()} notFound={t("ai.team.chainNotFound")} />
  if (!chain.data) return <Skeleton className="h-64 w-full" />
  const data = chain.data
  return <div className="max-w-4xl space-y-6"><PageHeader title={t("ai.team.chainTitle")} description={data.correlation_id} />
    {data.stopped_reason ? <p role="status" className="rounded-lg border border-destructive p-4">{t("ai.team.stopped", { reason: data.stopped_reason === "ai.chain_cost_limit" ? t("ai.team.costStopped", { cost: usdMicros(data.cost_limit_micros) }) : teamReason(t, data.stopped_reason) })}</p> : null}
    <dl className="grid gap-4 rounded-lg border p-4 sm:grid-cols-2">
      <div><dt>{t("ai.team.calls")}</dt><dd>{data.model_calls} / {data.max_model_calls}</dd></div>
      <div><dt>{t("ai.team.actions")}</dt><dd>{data.actions} / {data.max_actions}</dd></div>
      <div><dt>{t("ai.team.chainCost")}</dt><dd>{usdMicros(data.cost_micros)} / {usdMicros(data.cost_limit_micros)}</dd></div>
      <div><dt>{t("ai.team.depth")}</dt><dd>{t("ai.team.depthLimit", { count: data.max_depth })}</dd><dd className="text-xs text-muted-foreground">{t("ai.team.depthUnknown")}</dd></div>
    </dl>
    <ActivitySteps items={data.steps} handoffs />
  </div>
}
