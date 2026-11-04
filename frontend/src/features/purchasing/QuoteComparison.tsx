import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { Schemas } from "@/api/types"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { DataTable } from "@/components/common/DataTable"
import { MoneyText } from "@/components/common/MoneyText"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { describeError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"
import { useProcurementAction, useQuoteComparison } from "./hooks"
import { WRITE } from "./model"
import { ProposalFeedback } from "./ProposalFeedback"

export function QuoteComparison({ request }: { request: Schemas["QuoteRequestOut"] }) {
  const { t } = useTranslation()
  const query = useQuoteComparison(request.id)
  const action = useProcurementAction()
  const [conflict, setConflict] = useState(false)
  const name = (id: string) => request.suppliers.find((s) => s.supplier_id === id)?.supplier_name ?? id
  return <section className="min-w-0 space-y-3" aria-labelledby="comparison-title"><h2 id="comparison-title" className="text-lg font-semibold">{t("procurement.comparison")}</h2>
    <p className="text-sm">{t("procurement.formula")}</p><p className="text-sm text-muted-foreground">{t("procurement.formulaDetails")}</p>
    {query.data ? <p className="text-sm">{t("procurement.asOf", { date: query.data.as_of })}</p> : null}
    <DataTable<Schemas["QuoteScore"]> wrapHeaders caption={t("procurement.comparison")} data={query.data?.ranked} isLoading={query.isPending} error={query.error} onRetry={() => void query.refetch()} emptyTitle={t("procurement.noEligible")} columns={[
      { header: t("procurement.rank"), cell: ({ row }) => row.index + 1 },
      { header: t("purchasing.supplier"), accessorKey: "supplier_name" },
      { header: t("purchasing.total"), cell: ({ row }) => <MoneyText value={{ amount_minor: row.original.total_minor, currency: row.original.currency }} /> },
      { header: t("purchasing.leadTime"), accessorKey: "lead_time_days" },
      { header: t("procurement.arrival"), cell: ({ row }) => <span className="whitespace-nowrap">{row.original.delivery_date}</span> },
      { header: t("procurement.daysLate"), accessorKey: "days_late" },
      { header: t("procurement.priceComponent"), cell: ({ row }) => row.original.price_score.toFixed(3) },
      { header: t("procurement.leadComponent"), cell: ({ row }) => row.original.lead_score.toFixed(3) },
      { header: t("procurement.reliability"), cell: ({ row }) => <>{Math.round(row.original.reliability * 100)}%<p className="text-xs">{row.original.orders_count < 3 ? t("procurement.defaultReliability", { n: row.original.orders_count }) : t("procurement.observedReliability", { n: row.original.orders_count })}</p></> },
      { header: t("procurement.score"), cell: ({ row }) => row.original.score.toFixed(3) },
      { id: "create", header: t("purchasing.actions"), cell: ({ row }) => <PermissionGate permission={WRITE}>{request.status === "sent" ? <Button size="sm" className="h-auto min-h-8 min-w-40 whitespace-normal py-1.5 text-left" disabled={action.isPending || !!action.data || conflict} onClick={() => action.mutate({ kind: "po", body: { quote_id: row.original.quote_id, quote_version: row.original.quote_version } }, { onError: (e) => { if (e instanceof ApiProblem && e.isConflict) setConflict(true) } })}>{t("procurement.createPO")}</Button> : null}</PermissionGate> },
    ]} />
    {query.data?.ineligible.map((q) => <p key={q.quote_id}>{name(q.supplier_id)}: {q.reasons.map((r) => t(`procurement.reason.${r}`)).join(", ")}</p>)}
    {action.error ? <p role="alert">{describeError(action.error)}</p> : null}
    {action.data ? <ProposalFeedback proposal={action.data} /> : null}
    <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { void query.refetch().then((r) => { if (!r.error) { setConflict(false); action.reset() } }) }} />
    {conflict && query.error ? <QueryError error={query.error} onRetry={() => query.refetch()} /> : null}
  </section>
}
