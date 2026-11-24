import { Link } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { SalesReport as Report } from "@/api/types"
import { MoneyText } from "@/components/common/MoneyText"
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { formatQuantity } from "@/lib/quantity"
import { useSalesReport } from "../hooks"
import { decimalAmount, type Period, type RangeInput } from "../model"
import { ChoiceField, Note, RangeFields, ReportBody, Toolbar } from "./controls"
import { usePeriodOptions, useRowLabel } from "./labels"

type Group = "period" | "customer" | "item"

export function SalesReport({ range, onRangeChange }: { range: RangeInput; onRangeChange: (r: RangeInput) => void }) {
  const { t } = useTranslation()
  const [groupBy, setGroupBy] = useState<Group>("period")
  const [period, setPeriod] = useState<Period>("month")
  const query = useSalesReport(range, groupBy, period)
  const label = useRowLabel()
  const periods = usePeriodOptions()
  const report = query.data
  const csv = report && report.rows.length ? {
    filename: `sales-by-${report.group_by}-${report.date_from}-${report.date_to}.csv`,
    rows: () => [
      [t(`reports.groups.${report.group_by}`), ...(report.group_by === "item" ? [t("reports.columns.quantity")] : []), t("reports.columns.currency"),
        t("reports.columns.invoices"), t("reports.columns.creditNotes"), t("reports.columns.net"), t("reports.columns.tax"), t("reports.columns.total")],
      ...report.rows.map((r) => [label(report.group_by, r.key, r.label, report.period), ...(report.group_by === "item" ? [r.quantity ?? ""] : []), r.currency,
        r.invoice_count, r.credit_note_count, decimalAmount(r.net_minor, r.currency), decimalAmount(r.tax_minor, r.currency), decimalAmount(r.total_minor, r.currency)]),
      ...report.totals.map((s) => [t("reports.total"), ...(report.group_by === "item" ? [""] : []), s.currency, s.invoice_count, s.credit_note_count,
        decimalAmount(s.net_minor, s.currency), decimalAmount(s.tax_minor, s.currency), decimalAmount(s.total_minor, s.currency)]),
    ],
  } : null
  return <section className="space-y-4" aria-label={t("reports.tabs.sales")}>
    <Toolbar csv={csv}>
      <RangeFields id="sales" range={range} onChange={onRangeChange} />
      <ChoiceField id="sales-group" label={t("reports.filters.groupBy")} value={groupBy} onChange={setGroupBy}
        options={(["period", "customer", "item"] as const).map((g) => ({ value: g, label: t(`reports.groups.${g}`) }))} />
      {groupBy === "period" ? <ChoiceField id="sales-period" label={t("reports.filters.period")} value={period} onChange={setPeriod} options={periods} /> : null}
    </Toolbar>
    <ReportBody query={query} isEmpty={(r) => r.rows.length === 0}>{(r) => <>
      {r.totals.map((total) => <CurrencyTable key={total.currency} report={r} total={total} />)}
    </>}</ReportBody>
    <Note>{t("reports.notes.sales")} <Link className="record-link" to="/sales">{t("reports.notes.receivables")}</Link></Note>
  </section>
}

function CurrencyTable({ report, total }: { report: Report; total: Report["totals"][number] }) {
  const { t } = useTranslation()
  const label = useRowLabel()
  const byItem = report.group_by === "item"
  const money = (amount: number) => <MoneyText value={{ amount_minor: amount, currency: total.currency }} />
  const rows = report.rows.filter((r) => r.currency === total.currency)
  return <Table>
    <caption className="text-left font-medium">{t("reports.caption", { report: t("reports.tabs.sales"), currency: total.currency })}</caption>
    <TableHeader><TableRow>
      <TableHead>{t(`reports.groups.${report.group_by}`)}</TableHead>
      {byItem ? <TableHead className="text-right">{t("reports.columns.quantity")}</TableHead> : null}
      <TableHead className="text-right">{t("reports.columns.invoices")}</TableHead><TableHead className="text-right">{t("reports.columns.creditNotes")}</TableHead>
      <TableHead className="text-right">{t("reports.columns.net")}</TableHead><TableHead className="text-right">{t("reports.columns.tax")}</TableHead>
      <TableHead className="text-right">{t("reports.columns.total")}</TableHead>
    </TableRow></TableHeader>
    <TableBody>{rows.map((r) => <TableRow key={r.key}>
      <TableCell className="whitespace-normal">{label(report.group_by, r.key, r.label, report.period)}</TableCell>
      {byItem ? <TableCell className="text-right tabular-nums">{r.quantity == null ? "—" : formatQuantity(r.quantity)}</TableCell> : null}
      <TableCell className="text-right tabular-nums">{r.invoice_count}</TableCell><TableCell className="text-right tabular-nums">{r.credit_note_count}</TableCell>
      <TableCell className="text-right">{money(r.net_minor)}</TableCell><TableCell className="text-right">{money(r.tax_minor)}</TableCell>
      <TableCell className="text-right">{money(r.total_minor)}</TableCell>
    </TableRow>)}</TableBody>
    <TableFooter><TableRow>
      <TableCell className="font-semibold">{t("reports.total")}</TableCell>{byItem ? <TableCell /> : null}
      <TableCell className="text-right tabular-nums">{total.invoice_count}</TableCell><TableCell className="text-right tabular-nums">{total.credit_note_count}</TableCell>
      <TableCell className="text-right font-semibold">{money(total.net_minor)}</TableCell><TableCell className="text-right font-semibold">{money(total.tax_minor)}</TableCell>
      <TableCell className="text-right font-semibold">{money(total.total_minor)}</TableCell>
    </TableRow></TableFooter>
  </Table>
}
