// The pay link card on an issued invoice (ADR-022): copy the link for the customer, replace it, see attempts (phase 2);
// pay money back to the card through Approvals, see card refunds and disputes (phase 3); Stripe's fee (phase 4).
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { LocalTime } from "@/components/common/LocalTime"
import { MoneyText } from "@/components/common/MoneyText"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useBusiness } from "@/features/org/hooks"
import { toastError } from "@/lib/errors"
import { formatMoney } from "@/lib/money"
import { LINK_SHARE, REFUND_PROPOSE, useCardRefund, useInvoiceOnlinePayments, usePayLink, type InvoiceOnlinePayments, type RefundOption } from "./api"

/** Shown on every invoice to people who may share pay links; says nothing when there is nothing to do. */
export function InvoicePayLink({ invoiceId, payable }: { invoiceId: string; payable: boolean }) {
  return <PermissionGate permission={LINK_SHARE}><Card invoiceId={invoiceId} payable={payable} /></PermissionGate>
}

function Card({ invoiceId, payable }: { invoiceId: string; payable: boolean }) {
  const { t } = useTranslation()
  const query = useInvoiceOnlinePayments(invoiceId)
  const data = query.data
  if (!data) return null
  const requests = data.requests ?? []
  const note = !payable ? null : data.unavailable_reason === "not_configured" || data.unavailable_reason === "not_connected" ? t("payments.invoice.setUp")
    : data.unavailable_reason === "not_active" ? t("payments.invoice.notActive") : null
  if (!data.available && !note && !requests.length) return null
  return <section className="space-y-3 rounded-md border p-4 print:hidden" aria-label={t("payments.invoice.title")}>
    <div className="space-y-1">
      <h3 className="font-medium">{t("payments.invoice.title")}</h3>
      {data.available ? <p className="text-sm text-muted-foreground">{t("payments.invoice.intro")}</p> : null}
      {note ? <p className="text-sm text-muted-foreground">{note}</p> : null}
    </div>
    <Disputes data={data} />
    {data.available ? <Share data={data} invoiceId={invoiceId} /> : null}
    {requests.length ? <Attempts data={data} /> : null}
    {data.refund_options?.length ? <PermissionGate permission={REFUND_PROPOSE}><RefundAsk data={data} invoiceId={invoiceId} /></PermissionGate> : null}
    {data.refunds?.length ? <Refunds data={data} /> : null}
  </section>
}

function Share({ data, invoiceId }: { data: InvoiceOnlinePayments; invoiceId: string }) {
  const { t } = useTranslation()
  const mutation = usePayLink(invoiceId)
  const [replacing, setReplacing] = useState(false)
  const zone = useBusiness().data?.timezone ?? "UTC"
  const copy = async () => {
    try {
      const url = data.link_url ?? (await mutation.mutateAsync(false)).url
      await navigator.clipboard.writeText(url).then(() => toast.success(t("payments.invoice.copied")), () => toast.error(t("payments.invoice.copyFailed")))
    } catch (e) { toastError(e) }
  }
  return <div className="space-y-2">
    {data.mode === "test" ? <p className="text-sm font-medium">{t("payments.invoice.testMode")}</p> : null}
    <div className="flex flex-wrap items-center gap-2">
      <Button disabled={mutation.isPending} onClick={() => { void copy() }}>{t("payments.invoice.copy")}</Button>
      {data.link_url ? <Button variant="outline" disabled={mutation.isPending} onClick={() => setReplacing(true)}>{t("payments.invoice.replace")}</Button> : null}
    </div>
    {data.link_url ? <Input readOnly aria-label={t("payments.invoice.linkLabel")} value={data.link_url} onFocus={(e) => e.currentTarget.select()} /> : null}
    {data.link_last_opened_at ? <p className="text-sm text-muted-foreground">{t("payments.invoice.opened")}: <LocalTime iso={data.link_last_opened_at} timeZone={zone} /></p> : null}
    <ConfirmDialog open={replacing} onOpenChange={setReplacing} destructive pending={mutation.isPending}
      title={t("payments.invoice.replaceTitle")} description={t("payments.invoice.replaceBody")} confirmLabel={t("payments.invoice.replace")}
      onConfirm={() => mutation.mutate(true, {
        onSuccess: () => { setReplacing(false); toast.success(t("payments.invoice.replaced")) },
        onError: (e) => { setReplacing(false); toastError(e) },
      })} />
  </div>
}

function Attempts({ data }: { data: InvoiceOnlinePayments }) {
  const { t } = useTranslation()
  const zone = useBusiness().data?.timezone ?? "UTC"
  return <div className="space-y-2">
    <h4 className="text-sm font-medium">{t("payments.invoice.history")}</h4>
    <Table>
      <TableHeader><TableRow>
        <TableHead>{t("payments.invoice.date")}</TableHead><TableHead>{t("payments.invoice.amount")}</TableHead><TableHead>{t("payments.invoice.state")}</TableHead>
      </TableRow></TableHeader>
      <TableBody>{(data.requests ?? []).map((r) => <TableRow key={r.id}>
        <TableCell><LocalTime iso={r.created_at} timeZone={zone} /></TableCell>
        <TableCell><MoneyText value={{ amount_minor: r.amount_minor, currency: r.currency }} /></TableCell>
        <TableCell className="whitespace-normal">
          {t(`payments.invoice.states.${r.status === "creating" ? "open" : r.status}`)}
          {r.stripe_fee_minor != null && r.net_minor != null ? <p className="mt-1 text-xs text-muted-foreground">{t("payments.invoice.fee", { fee: formatMoney({ amount_minor: r.stripe_fee_minor, currency: r.currency }), net: formatMoney({ amount_minor: r.net_minor, currency: r.currency }) })}</p> : null}
          {r.surplus_minor > 0 ? <p role="note" className="mt-1 text-sm">{t("payments.invoice.surplus", { amount: formatMoney({ amount_minor: r.surplus_minor, currency: r.currency }) })}</p> : null}
        </TableCell>
      </TableRow>)}</TableBody>
    </Table>
  </div>
}

function Disputes({ data }: { data: InvoiceOnlinePayments }) {
  const { t } = useTranslation()
  const zone = useBusiness().data?.timezone ?? "UTC"
  return <>{(data.disputes ?? []).map((d) => {
    const amount = formatMoney({ amount_minor: d.amount_minor, currency: d.currency })
    if (d.outcome === "open") {
      return <div key={d.id} role="alert" className="space-y-1 rounded-md border border-destructive p-3 text-sm">
        <p>{t("payments.invoice.dispute", { amount })}</p>
        {d.due_by ? <p className="font-medium">{t("payments.invoice.disputeDue")}: <LocalTime iso={d.due_by} timeZone={zone} /></p> : null}
      </div>
    }
    const text = d.outcome === "won" ? t("payments.invoice.disputeWon", { amount })
      : d.last_error ? t("payments.invoice.disputeError", { amount }) : t("payments.invoice.disputeLost", { amount })
    return <p key={d.id} role="note" className="text-sm">{text}</p>
  })}</>
}

function RefundAsk({ data, invoiceId }: { data: InvoiceOnlinePayments; invoiceId: string }) {
  const { t } = useTranslation()
  const mutation = useCardRefund(invoiceId)
  const [asking, setAsking] = useState<RefundOption | null>(null)
  const money = (o: RefundOption) => formatMoney({ amount_minor: o.amount_minor, currency: o.currency })
  return <div className="space-y-2">
    {(data.refund_options ?? []).map((o) => <div key={`${o.request_id}:${o.source}:${o.invoice_refund_id ?? ""}`} className="flex flex-wrap items-center gap-2">
      <Button variant="outline" disabled={mutation.isPending} onClick={() => setAsking(o)}>{t("payments.invoice.refundAsk", { amount: money(o) })}</Button>
      <span className="text-sm text-muted-foreground">{t(`payments.invoice.refundWhy.${o.source}`, { number: o.credit_note_number ?? "" })}</span>
    </div>)}
    <ConfirmDialog open={asking !== null} onOpenChange={(open) => { if (!open) setAsking(null) }} pending={mutation.isPending}
      title={asking ? t("payments.invoice.refundAskTitle", { amount: money(asking) }) : ""} description={t("payments.invoice.refundAskBody")}
      confirmLabel={t("payments.invoice.refundAskConfirm")}
      onConfirm={() => { if (asking) mutation.mutate(asking, {
        onSuccess: () => { setAsking(null); toast.success(t("payments.invoice.refundAsked")) },
        onError: (e) => { setAsking(null); toastError(e) },
      }) }} />
  </div>
}

function Refunds({ data }: { data: InvoiceOnlinePayments }) {
  const { t } = useTranslation()
  const zone = useBusiness().data?.timezone ?? "UTC"
  return <div className="space-y-2">
    <h4 className="text-sm font-medium">{t("payments.invoice.refunds")}</h4>
    <Table>
      <TableHeader><TableRow>
        <TableHead>{t("payments.invoice.date")}</TableHead><TableHead>{t("payments.invoice.amount")}</TableHead><TableHead>{t("payments.invoice.state")}</TableHead>
      </TableRow></TableHeader>
      <TableBody>{(data.refunds ?? []).map((r) => <TableRow key={r.id}>
        <TableCell><LocalTime iso={r.created_at} timeZone={zone} /></TableCell>
        <TableCell><MoneyText value={{ amount_minor: r.amount_minor, currency: r.currency }} /></TableCell>
        <TableCell className="whitespace-normal">
          {t(`payments.invoice.refundStates.${r.status}`)}
          <p className="text-sm text-muted-foreground">{t(`payments.invoice.refundSources.${r.source}`, { number: r.credit_note_number ?? "" })}</p>
        </TableCell>
      </TableRow>)}</TableBody>
    </Table>
  </div>
}
