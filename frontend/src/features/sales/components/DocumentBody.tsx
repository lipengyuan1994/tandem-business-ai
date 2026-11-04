import { formatQuantity } from "@/lib/quantity"
import { Link } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"
import type { SalesDocument } from "@/api/types"
import { useSession } from "@/app/session-context"
import { LocalTime } from "@/components/common/LocalTime"
import { MoneyText } from "@/components/common/MoneyText"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { DocumentFamily } from "../model"

export function DocumentBody({ document: d, family }: { document: SalesDocument; family: DocumentFamily }) {
  const { t } = useTranslation()
  const { businessName, businessTimeZone } = useSession()
  const money = (amount: number) => <MoneyText value={{ amount_minor: amount, currency: d.currency }} />
  return <article className="sales-document space-y-6" aria-label={t("sales.printable")}>
    <header className="space-y-1">
      <h2 className="text-xl font-semibold">{businessName}</h2>
      <p>{t(d.kind === "credit_note" ? "sales.creditNote" : `sales.documentTypes.${family}`)} · {d.number ?? t("status.draft")}</p>
      <StatusBadge status={d.status} />
    </header>
    <div className="grid gap-4 sm:grid-cols-2">
      <section aria-label={t("sales.billTo")}>
        <h3 className="font-medium">{t("sales.billTo")}</h3>
        <p>{d.bill_to_name}</p>
        {d.bill_to_email ? <p>{d.bill_to_email}</p> : null}
        {d.bill_to_address ? <p className="whitespace-pre-wrap">{d.bill_to_address}</p> : null}
      </section>
      <dl className="grid grid-cols-2 gap-2 text-sm">
        <dt>{t("sales.fields.created")}</dt><dd><LocalTime iso={d.created_at} timeZone={businessTimeZone} pattern="d MMM yyyy" /></dd>
        <dt>{t("sales.fields.issueDate")}</dt><dd>{d.issue_date ?? t("sales.unissued")}</dd>
        <dt>{t("sales.fields.dueDate")}</dt><dd>{d.due_date ?? "—"}</dd>
        {d.paid_at ? <><dt>{t("sales.fields.paidAt")}</dt><dd><LocalTime iso={d.paid_at} timeZone={businessTimeZone} /></dd></> : null}
      </dl>
    </div>
    {d.credit_of_invoice_id ? <p>{t("sales.creditOf")} <Link className="underline" to="/sales/invoices/$id" params={{ id: d.credit_of_invoice_id }}>{t("sales.originalInvoice")}</Link></p> : null}
    <Table>
      <caption className="sr-only">{t("sales.lines")}</caption>
      <TableHeader><TableRow>
        {["description", "quantity", "unitPrice", "taxPercent", "subtotal", "tax", "total"].map((key) => <TableHead key={key}>{t(`sales.fields.${key}`)}</TableHead>)}
      </TableRow></TableHeader>
      <TableBody>{d.lines?.map((l) => <TableRow key={l.id}>
        <TableCell className="whitespace-normal">{l.name}</TableCell><TableCell>{formatQuantity(l.quantity)}</TableCell><TableCell>{money(l.unit_price_minor)}</TableCell>
        <TableCell>{t("sales.percent", { value: Math.round(Number(l.tax_rate) * 10000) / 100 })}</TableCell>
        <TableCell>{money(l.line_subtotal_minor)}</TableCell><TableCell>{money(l.line_tax_minor)}</TableCell><TableCell>{money(l.line_total_minor)}</TableCell>
      </TableRow>)}</TableBody>
    </Table>
    <dl className="ml-auto grid max-w-sm grid-cols-2 gap-2 border-t pt-3 text-sm">
      <dt>{t("sales.fields.subtotal")}</dt><dd className="text-right">{money(d.subtotal_minor)}</dd>
      <dt>{t("sales.fields.tax")}</dt><dd className="text-right">{money(d.tax_minor)}</dd>
      <dt className="font-semibold">{t("sales.fields.total")}</dt><dd className="text-right font-semibold">{money(d.total_minor)}</dd>
    </dl>
    {d.notes ? <section><h3 className="font-medium">{t("sales.fields.notes")}</h3><p className="whitespace-pre-wrap text-sm">{d.notes}</p></section> : null}
  </article>
}
