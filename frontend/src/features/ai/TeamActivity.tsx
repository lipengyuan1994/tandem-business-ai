import { useEffect, useRef } from "react"
import { ArrowRight, Bot, ClipboardList, Mail, ShieldCheck, Zap } from "lucide-react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { QueryError } from "@/components/common/QueryError"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { DeliveryBadge } from "./DeliveryStatus"
import { useAgents, useTeamActivity, type TeamItem } from "./team-api"
import { teamReason, usdMicros } from "./team-text"

const ICONS = { run: Bot, proposal: ClipboardList, evaluation: ShieldCheck, decision: ShieldCheck, execution: Zap, delivery: Mail }

export function TeamActivity({ agentKey }: { agentKey: string }) {
  const { t } = useTranslation()
  const activity = useTeamActivity(agentKey)
  const sentinel = useRef<HTMLDivElement>(null)
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = activity
  useEffect(() => {
    if (!sentinel.current || !hasNextPage || isFetchingNextPage || isFetchNextPageError || !globalThis.IntersectionObserver) return
    const observer = new IntersectionObserver(([entry]) => { if (entry?.isIntersecting) void fetchNextPage() }, { rootMargin: "200px" })
    observer.observe(sentinel.current)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage])
  if (activity.isPending) return <Skeleton className="h-48 w-full" />
  return <section className="space-y-4" aria-label={t("ai.team.timeline")}>
    {activity.error ? <QueryError error={activity.error} onRetry={() => activity.isFetchNextPageError ? activity.fetchNextPage() : activity.refetch()} /> : null}
    <ActivitySteps items={activity.data?.pages.flatMap((page) => page.items) ?? []} />
    <div ref={sentinel} />
    {hasNextPage ? <Button variant="outline" disabled={isFetchingNextPage} onClick={() => void fetchNextPage()}>{t(isFetchingNextPage ? "common.loading" : "common.loadMore")}</Button> : null}
  </section>
}

export function ActivitySteps({ items, handoffs = false }: { items: TeamItem[]; handoffs?: boolean }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const agents = useAgents()
  const name = (key: string) => agents.data?.items.find((a) => a.key === key)?.name ?? t(`ai.team.names.${key}`, { defaultValue: key.replaceAll("_", " ") })
  if (!items.length) return <EmptyState title={t("ai.team.emptyActivity")} />
  return <ol className="space-y-4" aria-label={t("ai.team.timeline")}>{items.map((item, index) => {
    const Icon = ICONS[item.kind as keyof typeof ICONS] ?? Bot
    const previous = items[index - 1]?.agent
    return <li key={`${item.kind}:${item.id}`} className="space-y-2 rounded-lg border p-4">
      {handoffs && previous && item.agent && previous !== item.agent ? <p className="flex items-center gap-2 text-sm font-medium">{name(previous)} <ArrowRight aria-label={t("ai.team.handoff")} className="size-4" /> {name(item.agent)}</p> : null}
      <div className="flex flex-wrap items-center gap-2"><Icon aria-hidden="true" className="size-4" /><span className="text-sm text-muted-foreground">{t(`ai.team.kinds.${item.kind}`, { defaultValue: item.kind.replaceAll("_", " ") })}</span><h3 className="font-medium">{item.title}</h3><StatusBadge status={item.status} /></div>
      <div className="flex flex-wrap items-center gap-2 text-sm"><LocalTime iso={item.created_at} timeZone={businessTimeZone} pattern="d MMM yyyy HH:mm" />{item.agent ? <a className="record-link" href={`/ai/team/${encodeURIComponent(item.agent)}`}>{name(item.agent)}</a> : null}<span>{t("ai.team.itemCost", { cost: usdMicros(item.cost_micros) })}</span>{item.delivery_state !== "none" ? <DeliveryBadge state={item.delivery_state} /> : null}</div>
      {item.reasons?.length ? <ul className="space-y-1 text-sm">{item.reasons.map((reason) => <li key={reason}>{teamReason(t, reason)}</li>)}</ul> : null}
      <div className="flex flex-wrap gap-4 text-sm">
        {item.proposal_id ? <a className="record-link" href={`/approvals/${encodeURIComponent(item.proposal_id)}`}>{t("ai.team.viewProposal")}</a> : null}
        {item.result_link?.startsWith("/") && !item.result_link.startsWith("//") ? <a className="record-link" href={item.result_link}>{t("ai.team.viewResult")}</a> : null}
        {item.correlation_id ? <a className="record-link" href={`/ai/chains/${encodeURIComponent(item.correlation_id)}`}>{t("ai.team.viewChain")}</a> : null}
      </div>
    </li>
  })}</ol>
}
