import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { SpendReport as Report } from "@/api/types"
import { MoneyText } from "@/components/common/MoneyText"
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useSpendReport } from "../hooks"
import { decimalAmount, type Period, type RangeInput } from "../model"
import { ChoiceField, Note, RangeFields, ReportBody, Toolbar } from "./controls"
import { usePeriodOptions, useRowLabel } from "./labels"

type Group = "supplier" | "period"

export function SpendReport({ range, onRangeChange }: { range: RangeInput; onRangeChange: (r: RangeInput) => void }) {
  const { t } = useTranslation()
  const [groupBy, setGroupBy] = useState<Group>("supplier")
  const [period, setPeriod] = useState<Period>("month")
  const query = useSpendReport(range, groupBy, period)
  const label = useRowLabel()
  const periods = usePeriodOptions()
  const report = query.data
  const csv = report && report.rows.length ? {
    filename: `spend-by-${report.group_by}-${report.date_from}-${report.date_to}.csv`,
    rows: () => [
      [t(`reports.groups.${report.group_by}`), t("reports.columns.currency"), t("reports.columns.orders"), t("reports.columns.ordered"), t("reports.columns.receipts"), t("reports.columns.received")],
      ...report.rows.map((r) => [label(report.group_by, r.key, r.label, report.period), r.currency, r.order_count, decimalAmount(r.ordered_minor, r.currency), r.receipt_count, decimalAmount(r.received_minor, r.currency)]),
      ...report.totals.map((s) => [t("reports.total"), s.currency, s.order_count, decimalAmount(s.ordered_minor, s.currency), s.receipt_count, decimalAmount(s.received_minor, s.currency)]),
    ],
  } : null
  return <section className="space-y-4" aria-label={t("reports.tabs.spend")}>
    <Toolbar csv={csv}>
      <RangeFields id="spend" range={range} onChange={onRangeChange} />
      <ChoiceField id="spend-group" label={t("reports.filters.groupBy")} value={groupBy} onChange={setGroupBy}
        options={(["supplier", "period"] as const).map((g) => ({ value: g, label: t(`reports.groups.${g}`) }))} />
      {groupBy === "period" ? <ChoiceField id="spend-period" label={t("reports.filters.period")} value={period} onChange={setPeriod} options={periods} /> : null}
    </Toolbar>
    <ReportBody query={query} isEmpty={(r) => r.rows.length === 0}>{(r) => <>
      {r.totals.map((total) => <CurrencyTable key={total.currency} report={r} total={total} />)}
    </>}</ReportBody>
    <Note>{t("reports.notes.spend")}</Note>
  </section>
}

function CurrencyTable({ report, total }: { report: Report; total: Report["totals"][number] }) {
  const { t } = useTranslation()
  const label = useRowLabel()
  const money = (amount: number) => <MoneyText value={{ amount_minor: amount, currency: total.currency }} />
  return <Table>
    <caption className="text-left font-medium">{t("reports.caption", { report: t("reports.tabs.spend"), currency: total.currency })}</caption>
    <TableHeader><TableRow>
      <TableHead>{t(`reports.groups.${report.group_by}`)}</TableHead>
      {(["orders", "ordered", "receipts", "received"] as const).map((c) => <TableHead key={c} className="text-right">{t(`reports.columns.${c}`)}</TableHead>)}
    </TableRow></TableHeader>
    <TableBody>{report.rows.filter((r) => r.currency === total.currency).map((r) => <TableRow key={r.key}>
      <TableCell className="whitespace-normal">{label(report.group_by, r.key, r.label, report.period)}</TableCell>
      <TableCell className="text-right tabular-nums">{r.order_count}</TableCell><TableCell className="text-right">{money(r.ordered_minor)}</TableCell>
      <TableCell className="text-right tabular-nums">{r.receipt_count}</TableCell><TableCell className="text-right">{money(r.received_minor)}</TableCell>
    </TableRow>)}</TableBody>
    <TableFooter><TableRow>
      <TableCell className="font-semibold">{t("reports.total")}</TableCell>
      <TableCell className="text-right tabular-nums">{total.order_count}</TableCell><TableCell className="text-right font-semibold">{money(total.ordered_minor)}</TableCell>
      <TableCell className="text-right tabular-nums">{total.receipt_count}</TableCell><TableCell className="text-right font-semibold">{money(total.received_minor)}</TableCell>
    </TableRow></TableFooter>
  </Table>
}
