import { BriefingCard } from "@/features/ai/BriefingCard"
import { useLateOrders } from "@/features/purchasing/hooks"
import { PermissionGate } from "@/components/common/PermissionGate"
import { READ } from "@/features/purchasing/model"
import { QueryError } from "@/components/common/QueryError"
import { Link } from "@tanstack/react-router"
import { lazy, Suspense, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import type { Appointment } from "@/api/types"
import { PageHeader } from "@/components/common/PageHeader"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { formatInZone } from "@/lib/dates"
import { useToday } from "./hooks"

const SuggestionsPanel = lazy(() => import("@/features/inventory/components/SuggestionsPanel").then((m) => ({ default: m.SuggestionsPanel })))

/** Cards appear only for sections the API returned, which follow the user's permissions (M11). */
export function TodayPage() {
  const { t } = useTranslation()
  const today = useToday()
  const d = today.data

  if (today.error) return <QueryError error={today.error} onRetry={() => today.refetch()} />
  if (today.isPending || !d) return <><PageHeader title={t("today.title")} /><Skeleton className="h-64 w-full" /></>
  return (
    <>
      <PageHeader title={t("today.title")} description={formatInZone(`${d.local_date}T12:00:00Z`, "UTC", "EEEE d MMMM yyyy")} />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <BriefingCard className="md:col-span-2 xl:col-span-3" />
        <PermissionGate permission={READ}><LateOrdersCard /></PermissionGate>
        {d.agenda ? <Section title={t("today.agenda")} className="xl:col-span-2"><Agenda items={d.agenda} timeZone={d.timezone} /></Section> : null}
        {d.approvals ? (
          <Section title={t("today.approvals", { n: d.approvals.can_decide })}>
            {d.approvals.top.length === 0 ? <p className="text-sm text-muted-foreground">{t("today.nothingPending")}</p> : (
              <ul className="space-y-1 text-sm">
                {d.approvals.top.map((p) => (
                  <li key={p.id}><Link className="underline" to="/approvals/$proposalId" params={{ proposalId: p.id }}>{p.title}</Link></li>
                ))}
              </ul>
            )}
          </Section>
        ) : null}
        {d.conversations ? (
          <Section title={t("nav.inbox")}>
            <dl className="grid grid-cols-2 gap-2 text-sm">
              {Object.entries(d.conversations).map(([status, n]) => (
                <div key={status}><dt className="text-muted-foreground">{t(`status.${status}`, { defaultValue: status })}</dt><dd className="text-lg font-semibold">{n}</dd></div>
              ))}
            </dl>
            <Link className="mt-2 inline-block text-sm underline" to="/inbox">{t("today.openInbox")}</Link>
          </Section>
        ) : null}
        {d.delivery_issues !== null && d.delivery_issues !== undefined ? (
          <Section title={t("today.deliveryIssues")} className="order-last md:order-none">
            <p className={d.delivery_issues > 0 ? "text-lg font-semibold text-destructive" : "text-sm text-muted-foreground"}>
              {d.delivery_issues > 0 ? t("today.deliveryIssuesCount", { n: d.delivery_issues }) : t("today.noDeliveryIssues")}
            </p>
          </Section>
        ) : null}
        {d.low_stock ? (d.low_stock.length ? <Suspense fallback={<Skeleton className="h-40 w-full" />}><SuggestionsPanel suggestions={d.low_stock} /></Suspense> : <Section title={t("inventory.suggestions.title")}><p className="text-sm text-muted-foreground">{t("today.stockOk")}</p></Section>) : null}
      </div>
    </>
  )
}

function Section({ title, className, children }: { title: string; className?: string; children: ReactNode }) {
  return (
    <Card className={className}>
      <CardHeader><CardTitle className="text-sm">{title}</CardTitle></CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

function Agenda({ items, timeZone }: { items: Appointment[]; timeZone: string }) {
  const { t } = useTranslation()
  if (!items.length) return <p className="text-sm text-muted-foreground">{t("today.noAppointments")}</p>
  const byResource = new Map<string, Appointment[]>()
  for (const a of items) byResource.set(a.resource_name, [...(byResource.get(a.resource_name) ?? []), a])
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {[...byResource].map(([resource, list]) => (
        <section key={resource} aria-label={resource}>
          <h3 className="mb-1 text-sm font-medium">{resource}</h3>
          <ol className="space-y-1 text-sm">
            {list.map((a) => (
              <li key={a.id} className="flex items-center gap-2">
                <span className="w-12 tabular-nums">{formatInZone(a.starts_at, timeZone, "HH:mm")}</span>
                <span className="flex-1 truncate">{a.service_name} · {a.customer_name}</span>
                <StatusBadge status={a.status} />
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  )
}

function LateOrdersCard() {
  const { t } = useTranslation()
  const query = useLateOrders()
  return <Section title={t("procurement.lateTitle")}>
    {query.isPending ? <Skeleton className="h-12" /> : query.error ? <QueryError error={query.error} onRetry={() => query.refetch()} /> : <p>{query.data?.length ? t("procurement.lateCount", { count: query.data.length, n: query.data.length }) : t("procurement.noLate")}</p>}
    <Link className="record-link" to="/purchasing" search={{ late: true }}>{t("procurement.openLate")}</Link>
  </Section>
}
