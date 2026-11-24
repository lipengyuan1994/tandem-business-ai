import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { MarginReport as Report } from "@/api/types"
import { MoneyText } from "@/components/common/MoneyText"
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { formatMoney } from "@/lib/money"
import { formatQuantity } from "@/lib/quantity"
import { useMarginReport } from "../hooks"
import { decimalAmount, percentText, type RangeInput } from "../model"
import { ChoiceField, Note, RangeFields, ReportBody, Toolbar } from "./controls"
import { useRowLabel } from "./labels"

type Group = "item" | "kind"
type Amounts = Pick<Report["totals"], "revenue_minor" | "cost_minor" | "margin_minor" | "margin_basis_points" | "uncosted_revenue_minor">

export function MarginReport({ range, onRangeChange }: { range: RangeInput; onRangeChange: (r: RangeInput) => void }) {
  const { t, i18n } = useTranslation()
  const [groupBy, setGroupBy] = useState<Group>("item")
  const query = useMarginReport(range, groupBy)
  const label = useRowLabel()
  const report = query.data
  const csv = report && report.rows.length ? {
    filename: `gross-margin-by-${report.group_by}-${report.date_from}-${report.date_to}.csv`,
    rows: () => {
      const money = (v: number | null) => v === null ? "" : decimalAmount(v, report.currency)
      const amounts = (a: Amounts) => [money(a.revenue_minor), money(a.cost_minor), money(a.margin_minor),
        a.margin_basis_points === null ? "" : (a.margin_basis_points / 100).toFixed(2), money(a.uncosted_revenue_minor)]
      return [
        [t(`reports.groups.${report.group_by}`), ...(report.group_by === "item" ? [t("reports.columns.quantity")] : []),
          `${t("reports.columns.revenue")} (${report.currency})`, t("reports.columns.cost"), t("reports.columns.margin"), t("reports.columns.marginPercent"), t("reports.columns.uncostedRevenue")],
        ...report.rows.map((r) => [label(report.group_by, r.key, r.label), ...(report.group_by === "item" ? [r.quantity ?? ""] : []), ...amounts(r)]),
        [t("reports.total"), ...(report.group_by === "item" ? [""] : []), ...amounts(report.totals)],
      ]
    },
  } : null
  return <section className="space-y-4" aria-label={t("reports.tabs.margin")}>
    <Toolbar csv={csv}>
      <RangeFields id="margin" range={range} onChange={onRangeChange} />
      <ChoiceField id="margin-group" label={t("reports.filters.groupBy")} value={groupBy} onChange={setGroupBy}
        options={(["item", "kind"] as const).map((g) => ({ value: g, label: t(`reports.groups.${g}`) }))} />
    </Toolbar>
    <ReportBody query={query} isEmpty={(r) => r.rows.length === 0}>{(r) => <>
      <MarginTable report={r} />
      {r.totals.uncosted_revenue_minor ? <Note>{t("reports.notes.uncosted", { amount: formatMoney({ amount_minor: r.totals.uncosted_revenue_minor, currency: r.currency }, i18n.language) })}</Note> : null}
      {r.other_currency_document_count ? <Note>{t("reports.notes.otherCurrency", { count: r.other_currency_document_count })}</Note> : null}
    </>}</ReportBody>
    <Note>{t("reports.notes.margin")}</Note>
  </section>
}

function MarginTable({ report }: { report: Report }) {
  const { t, i18n } = useTranslation()
  const label = useRowLabel()
  const byItem = report.group_by === "item"
  const money = (amount: number | null) => <MoneyText value={amount === null ? null : { amount_minor: amount, currency: report.currency }} />
  const cells = (a: Amounts, strong = false) => <>
    {[a.revenue_minor, a.cost_minor, a.margin_minor].map((v, i) => <TableCell key={i} className={strong ? "text-right font-semibold" : "text-right"}>{money(v)}</TableCell>)}
    <TableCell className="text-right tabular-nums">{percentText(a.margin_basis_points, i18n.language) ?? "—"}</TableCell>
    <TableCell className="text-right">{a.uncosted_revenue_minor ? money(a.uncosted_revenue_minor) : "—"}</TableCell>
  </>
  return <Table>
    <caption className="text-left font-medium">{t("reports.caption", { report: t("reports.tabs.margin"), currency: report.currency })}</caption>
    <TableHeader><TableRow>
      <TableHead>{t(`reports.groups.${report.group_by}`)}</TableHead>
      {byItem ? <TableHead className="text-right">{t("reports.columns.quantity")}</TableHead> : null}
      {(["revenue", "cost", "margin", "marginPercent", "uncostedRevenue"] as const).map((c) => <TableHead key={c} className="text-right">{t(`reports.columns.${c}`)}</TableHead>)}
    </TableRow></TableHeader>
    <TableBody>{report.rows.map((r) => <TableRow key={r.key}>
      <TableCell className="whitespace-normal">{label(report.group_by, r.key, r.label)}</TableCell>
      {byItem ? <TableCell className="text-right tabular-nums">{r.quantity == null ? "—" : formatQuantity(r.quantity)}</TableCell> : null}
      {cells(r)}
    </TableRow>)}</TableBody>
    <TableFooter><TableRow>
      <TableCell className="font-semibold">{t("reports.total")}</TableCell>{byItem ? <TableCell /> : null}
      {cells(report.totals, true)}
    </TableRow></TableFooter>
  </Table>
}
