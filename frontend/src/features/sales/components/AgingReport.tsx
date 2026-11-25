import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { AgingReport as Report } from "@/api/types"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { MoneyText } from "@/components/common/MoneyText"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useReceivablesAging } from "../hooks"
import { downloadCsv } from "@/lib/csv"
import { AGING_BUCKETS, receivablesCsv } from "../model"

type Row = Report["customers"][number]
type Total = Report["totals"][number]

/** Open invoice balances by customer and days past due. One table per currency. */
export function AgingReport() {
  const { t } = useTranslation()
  const [asOf, setAsOf] = useState("")
  const valid = asOf === "" || /^\d{4}-\d{2}-\d{2}$/.test(asOf)
  const query = useReceivablesAging(valid ? asOf : "")
  const report = query.data
  return <section className="space-y-4" aria-label={t("sales.aging.title")}>
    <div className="flex flex-wrap items-end gap-3">
      <div className="space-y-1"><Label htmlFor="aging-as-of">{t("sales.aging.asOf")}</Label>
        <Input id="aging-as-of" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></div>
      {report ? <p className="text-sm text-muted-foreground">{t("sales.aging.hint", { date: report.as_of })}</p> : null}
      {report && report.totals.length ? <Button type="button" variant="outline" className="ml-auto"
        onClick={() => downloadCsv(`receivables-aging-${report.as_of}.csv`, receivablesCsv(report, t))}>
        {t("sales.aging.download")}</Button> : null}
    </div>
    {query.isPending ? <Skeleton className="h-40 w-full" /> : query.error ? <QueryError error={query.error} onRetry={() => void query.refetch()} />
      : report && report.totals.length ? report.totals.map((total) => <CurrencyTable key={total.currency} total={total} rows={report.customers.filter((c) => c.currency === total.currency)} />)
        : <p className="text-sm">{t("sales.aging.empty")}</p>}
  </section>
}

function CurrencyTable({ rows, total }: { rows: Row[]; total: Total }) {
  const { t } = useTranslation()
  const money = (amount: number) => <MoneyText value={{ amount_minor: amount, currency: total.currency }} />
  return <Table>
    <caption className="text-left font-medium">{t("sales.aging.caption", { currency: total.currency })}</caption>
    <TableHeader><TableRow>
      <TableHead>{t("sales.fields.customer")}</TableHead><TableHead>{t("sales.aging.invoices")}</TableHead>
      {AGING_BUCKETS.map((b) => <TableHead key={b}>{t(`sales.aging.buckets.${b}`)}</TableHead>)}
      <TableHead>{t("sales.fields.total")}</TableHead>
    </TableRow></TableHeader>
    <TableBody>{rows.map((r) => <TableRow key={r.customer_id}>
      <TableCell className="whitespace-normal">{r.customer_name}</TableCell><TableCell>{r.invoice_count}</TableCell>
      {AGING_BUCKETS.map((b) => <TableCell key={b}>{money(r[b])}</TableCell>)}
      <TableCell className="font-semibold">{money(r.total_minor)}</TableCell>
    </TableRow>)}</TableBody>
    <TableFooter><TableRow>
      <TableCell className="font-semibold">{t("sales.aging.total")}</TableCell><TableCell>{total.invoice_count}</TableCell>
      {AGING_BUCKETS.map((b) => <TableCell key={b} className="font-semibold">{money(total[b])}</TableCell>)}
      <TableCell className="font-semibold">{money(total.total_minor)}</TableCell>
    </TableRow></TableFooter>
  </Table>
}
