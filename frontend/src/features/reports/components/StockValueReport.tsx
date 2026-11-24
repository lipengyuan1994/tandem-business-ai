import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { StockValueReport as Report } from "@/api/types"
import { MoneyText } from "@/components/common/MoneyText"
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { formatQuantity } from "@/lib/quantity"
import { useStockValueReport } from "../hooks"
import { decimalAmount, isDate } from "../model"
import { ChoiceField, DateField, Note, ReportBody, Toolbar } from "./controls"

type Group = "item" | "location"

export function StockValueReport() {
  const { t } = useTranslation()
  const [asOf, setAsOf] = useState("")
  const [groupBy, setGroupBy] = useState<Group>("item")
  const query = useStockValueReport(isDate(asOf) ? asOf : "", groupBy)
  const report = query.data
  const csv = report && report.rows.length ? {
    filename: `stock-value-by-${report.group_by}-${report.as_of}.csv`,
    rows: () => {
      const money = (v: number | null | undefined) => v == null ? "" : decimalAmount(v, report.currency)
      return report.group_by === "item" ? [
        [t("reports.groups.item"), t("reports.columns.sku"), t("reports.columns.onHand"), t("reports.columns.unit"), t("reports.columns.unitCost"), `${t("reports.columns.value")} (${report.currency})`],
        ...report.rows.map((r) => [r.label, r.sku ?? "", r.quantity ?? "", r.unit ?? "", money(r.unit_cost_minor), money(r.value_minor)]),
        [t("reports.total"), "", "", "", "", money(report.totals.value_minor)],
      ] : [
        [t("reports.groups.location"), t("reports.columns.items"), t("reports.columns.uncostedItems"), `${t("reports.columns.value")} (${report.currency})`],
        ...report.rows.map((r) => [r.label, r.item_count, r.uncosted_item_count, money(r.value_minor)]),
        [t("reports.total"), report.totals.item_count, report.totals.uncosted_item_count, money(report.totals.value_minor)],
      ]
    },
  } : null
  return <section className="space-y-4" aria-label={t("reports.tabs.stock")}>
    <Toolbar csv={csv}>
      <DateField id="stock-as-of" label={t("reports.filters.asOf")} value={asOf} onChange={setAsOf} />
      <ChoiceField id="stock-group" label={t("reports.filters.groupBy")} value={groupBy} onChange={setGroupBy}
        options={(["item", "location"] as const).map((g) => ({ value: g, label: t(`reports.groups.${g}`) }))} />
    </Toolbar>
    <ReportBody query={query} isEmpty={(r) => r.rows.length === 0}>{(r) => <>
      <StockTable report={r} />
      {r.totals.uncosted_item_count ? <Note>{t("reports.notes.uncostedItems", { count: r.totals.uncosted_item_count })}</Note> : null}
    </>}</ReportBody>
    <Note>{t("reports.notes.stock")}</Note>
  </section>
}

function StockTable({ report }: { report: Report }) {
  const { t } = useTranslation()
  const money = (amount: number | null | undefined) => <MoneyText value={amount == null ? null : { amount_minor: amount, currency: report.currency }} />
  const byItem = report.group_by === "item"
  return <Table>
    <caption className="text-left font-medium">{t("reports.caption", { report: t("reports.tabs.stock"), currency: report.currency })}</caption>
    <TableHeader><TableRow>
      <TableHead>{t(`reports.groups.${report.group_by}`)}</TableHead>
      {byItem ? <>
        <TableHead>{t("reports.columns.sku")}</TableHead><TableHead className="text-right">{t("reports.columns.onHand")}</TableHead>
        <TableHead className="text-right">{t("reports.columns.unitCost")}</TableHead>
      </> : <>
        <TableHead className="text-right">{t("reports.columns.items")}</TableHead><TableHead className="text-right">{t("reports.columns.uncostedItems")}</TableHead>
      </>}
      <TableHead className="text-right">{t("reports.columns.value")}</TableHead>
    </TableRow></TableHeader>
    <TableBody>{report.rows.map((r) => <TableRow key={r.key}>
      <TableCell className="whitespace-normal">{r.label || t("reports.unnamed")}</TableCell>
      {byItem ? <>
        <TableCell>{r.sku ?? "—"}</TableCell>
        <TableCell className="text-right tabular-nums">{r.quantity == null ? "—" : `${formatQuantity(r.quantity)}${r.unit ? ` ${r.unit}` : ""}`}</TableCell>
        <TableCell className="text-right">{money(r.unit_cost_minor)}</TableCell>
      </> : <>
        <TableCell className="text-right tabular-nums">{r.item_count}</TableCell><TableCell className="text-right tabular-nums">{r.uncosted_item_count}</TableCell>
      </>}
      <TableCell className="text-right">{r.uncosted_item_count && byItem ? "—" : money(r.value_minor)}</TableCell>
    </TableRow>)}</TableBody>
    <TableFooter><TableRow>
      <TableCell className="font-semibold">{t("reports.total")}</TableCell>
      {byItem ? <><TableCell /><TableCell /><TableCell /></> : <>
        <TableCell className="text-right tabular-nums">{report.totals.item_count}</TableCell><TableCell className="text-right tabular-nums">{report.totals.uncosted_item_count}</TableCell>
      </>}
      <TableCell className="text-right font-semibold">{money(report.totals.value_minor)}</TableCell>
    </TableRow></TableFooter>
  </Table>
}
