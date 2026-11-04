import { Link, useParams } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { MoneyText } from "@/components/common/MoneyText"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { PasteSupplierReplyButton } from "@/features/ai/PasteSupplierReplyDialog"
import { useQuoteRequest } from "./hooks"
import { READ, WRITE } from "./model"
import { EnterQuoteDialog, FollowupDialog } from "./ProcurementDialogs"
import { QuoteComparison } from "./QuoteComparison"
import { CopyLinkButton } from "./SupplierLink"

export function QuoteRequestPage() {
  const { t } = useTranslation()
  const { id } = useParams({ from: "/app/purchasing/quote-requests/$id" })
  return <PermissionGate permission={READ} fallback={<EmptyState title={t("purchasing.noAccess")} />}><QuoteRequestDetail key={id} id={id} /></PermissionGate>
}
function QuoteRequestDetail({ id }: { id: string }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const query = useQuoteRequest(id)
  const [enter, setEnter] = useState<string | null>(null)
  const [follow, setFollow] = useState<string | null>(null)
  const d = query.data
  if (query.isPending) return <Skeleton className="h-96" />
  if (query.error && !d) return <QueryError error={query.error} onRetry={() => query.refetch()} />
  if (!d) return null
  return <div className="min-w-0 space-y-6">
    <Link className="record-link" to="/purchasing" search={{ tab: "quotes" }}>{t("purchasing.back")}</Link>
    <PageHeader title={t("procurement.detail")} description={d.lines.map((l) => `${l.name} × ${l.quantity}`).join(", ")} actions={<StatusBadge status={d.status} />} />
    {query.error ? <QueryError error={query.error} onRetry={() => query.refetch()} /> : null}
    <p>{t("procurement.needed")}: {d.needed_by} · {t("procurement.reply")}: {d.reply_by}</p>
    {d.message ? <p className="whitespace-pre-wrap">{d.message}</p> : null}
    <section className="space-y-3" aria-labelledby="invites-title"><h2 id="invites-title" className="text-lg font-semibold">{t("procurement.invited")}</h2>
      {d.suppliers.map((s) => <div key={s.id} className="flex flex-wrap items-center gap-3 rounded-md border p-3"><span>{s.supplier_name}</span><StatusBadge status={s.status} />
        {s.last_followup_at ? <span className="text-sm">{t("procurement.lastFollowup")} <LocalTime iso={s.last_followup_at} timeZone={businessTimeZone} /></span> : null}
        {d.status === "sent" ? <PermissionGate permission={WRITE}><Button variant="outline" onClick={() => setEnter(s.supplier_id)}>{t("procurement.enter")}</Button>{s.status === "invited" ? <><Button variant="outline" onClick={() => setFollow(s.supplier_id)}>{t("procurement.followup")}</Button><CopyLinkButton requestId={d.id} supplierId={s.supplier_id} supplierName={s.supplier_name} /></> : null}</PermissionGate> : null}
      </div>)}
    </section>
    <section className="space-y-3" aria-labelledby="received-title"><h2 id="received-title" className="text-lg font-semibold">{t("procurement.received")}</h2>
      {d.status === "sent" ? <PasteSupplierReplyButton request={d} /> : null}
      {!d.quotes.length ? <EmptyState title={t("procurement.noQuotes")} /> : d.quotes.map((q) => <article key={q.id} className="space-y-2 rounded-md border p-3">
        <h3 className="font-medium">{d.suppliers.find((s) => s.supplier_id === q.supplier_id)?.supplier_name}</h3><StatusBadge status={q.status} />
        <ul>{q.lines.map((l) => <li key={l.item_id}>{d.lines.find((r) => r.item_id === l.item_id)?.name}: <MoneyText value={{ amount_minor: l.unit_price_minor, currency: q.currency }} />{l.quantity_available != null ? ` · ${t("procurement.available", { n: l.quantity_available })}` : null}</li>)}</ul>
        <p>{t("purchasing.leadTime")}: {q.lead_time_days} · {t("procurement.validUntil")}: {q.valid_until ?? t("procurement.unspecified")}</p>
        {q.min_quantity ? <p>{t("procurement.minimum", { n: q.min_quantity })}</p> : null}{q.notes ? <p className="whitespace-pre-wrap">{q.notes}</p> : null}
      </article>)}
    </section>
    <QuoteComparison request={d} />
    {enter ? <PermissionGate permission={WRITE}><EnterQuoteDialog request={d} supplier={enter} onClose={() => setEnter(null)} /></PermissionGate> : null}
    {follow ? <PermissionGate permission={WRITE}><FollowupDialog supplier={follow} about={{ quote_request_id: id }} onClose={() => setFollow(null)} /></PermissionGate> : null}
  </div>
}
