import { Link, useNavigate } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { DataTable } from "@/components/common/DataTable"
import { MoneyText } from "@/components/common/MoneyText"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { downloadCsv } from "@/lib/csv"
import { BillDialog } from "./BillDialog"
import { useBills, usePayablesAging } from "./billHooks"
import { BILL_FILTERS, BILL_WRITE, PAYABLES_BUCKETS, payablesCsv, type Bill, type BillFilter, type PayablesReport } from "./billModel"

/** Supplier bills, unpaid first by default, with a button to record a new one. */
export function Bills() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [filter, setFilter] = useState<BillFilter>("unpaid")
  const [creating, setCreating] = useState(false)
  const list = useBills({ filter })
  const columns: ColumnDef<Bill, unknown>[] = [
    { header: t("bills.reference"), cell: ({ row }) => <Link className="record-link" to="/purchasing/bills/$id" params={{ id: row.original.id }}>{row.original.supplier_reference ?? t("bills.noReference")}</Link> },
    { header: t("purchasing.supplier"), accessorKey: "supplier_name" },
    { header: t("bills.billDate"), accessorKey: "bill_date" },
    { header: t("bills.dueDate"), cell: ({ row }) => row.original.due_date ?? "—" },
    { header: t("purchasing.status"), cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { header: t("bills.totalShort"), cell: ({ row }) => <MoneyText value={{ amount_minor: row.original.total_minor, currency: row.original.currency }} /> },
    { header: t("bills.balance"), cell: ({ row }) => <MoneyText value={{ amount_minor: row.original.balance_minor, currency: row.original.currency }} /> },
  ]
  return <div className="space-y-4">
    <div className="flex flex-wrap items-end gap-3">
      <div className="space-y-1">
        <Label htmlFor="bill-filter">{t("bills.show")}</Label>
        <select id="bill-filter" className="h-9 rounded-md border bg-background px-3 text-sm" value={filter} onChange={(e) => setFilter(e.target.value as BillFilter)}>
          {BILL_FILTERS.map((f) => <option key={f} value={f}>{t(`bills.filters.${f}`)}</option>)}
        </select>
      </div>
      <PermissionGate permission={BILL_WRITE}><Button onClick={() => setCreating(true)}>{t("bills.new")}</Button></PermissionGate>
    </div>
    <DataTable columns={columns} data={list.data?.pages.flatMap((p) => p.items)} isLoading={list.isPending} error={list.error} onRetry={() => void list.refetch()}
      hasMore={list.hasNextPage} onLoadMore={() => { if (!list.isFetchingNextPage) void list.fetchNextPage() }} caption={t("bills.tab")} emptyTitle={t("bills.empty")} />
    {creating ? <PermissionGate permission={BILL_WRITE}><BillDialog onClose={() => setCreating(false)} onSaved={(bill) => { void navigate({ to: "/purchasing/bills/$id", params: { id: bill.id } }) }} /></PermissionGate> : null}
  </div>
}

type Total = PayablesReport["totals"][number]
type Row = PayablesReport["suppliers"][number]

/** What the business owes suppliers, by supplier and days past due. One table per currency. */
export function PayablesAging() {
  const { t } = useTranslation()
  const [asOf, setAsOf] = useState("")
  const valid = asOf === "" || /^\d{4}-\d{2}-\d{2}$/.test(asOf)
  const query = usePayablesAging(valid ? asOf : "")
  const report = query.data
  return <section className="space-y-4" aria-label={t("payables.title")}>
    <div className="flex flex-wrap items-end gap-3">
      <div className="space-y-1"><Label htmlFor="payables-as-of">{t("sales.aging.asOf")}</Label>
        <Input id="payables-as-of" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></div>
      {report ? <p className="text-sm text-muted-foreground">{t("payables.hint", { date: report.as_of })}</p> : null}
      {report && report.totals.length ? <Button type="button" variant="outline" className="ml-auto"
        onClick={() => downloadCsv(`payables-aging-${report.as_of}.csv`, payablesCsv(report, t))}>{t("sales.aging.download")}</Button> : null}
    </div>
    {query.isPending ? <Skeleton className="h-40 w-full" /> : query.error ? <QueryError error={query.error} onRetry={() => void query.refetch()} />
      : report && report.totals.length ? report.totals.map((total) => <CurrencyTable key={total.currency} total={total} rows={report.suppliers.filter((s) => s.currency === total.currency)} />)
        : <p className="text-sm">{t("payables.empty")}</p>}
  </section>
}

function CurrencyTable({ rows, total }: { rows: Row[]; total: Total }) {
  const { t } = useTranslation()
  const money = (amount: number) => <MoneyText value={{ amount_minor: amount, currency: total.currency }} />
  return <Table>
    <caption className="text-left font-medium">{t("payables.caption", { currency: total.currency })}</caption>
    <TableHeader><TableRow>
      <TableHead>{t("purchasing.supplier")}</TableHead><TableHead>{t("payables.bills")}</TableHead>
      {PAYABLES_BUCKETS.map((b) => <TableHead key={b}>{t(`sales.aging.buckets.${b}`)}</TableHead>)}
      <TableHead>{t("bills.totalShort")}</TableHead>
    </TableRow></TableHeader>
    <TableBody>{rows.map((r) => <TableRow key={r.supplier_id}>
      <TableCell className="whitespace-normal">{r.supplier_name}</TableCell><TableCell>{r.bill_count}</TableCell>
      {PAYABLES_BUCKETS.map((b) => <TableCell key={b}>{money(r[b])}</TableCell>)}
      <TableCell className="font-semibold">{money(r.total_minor)}</TableCell>
    </TableRow>)}</TableBody>
    <TableFooter><TableRow>
      <TableCell className="font-semibold">{t("sales.aging.total")}</TableCell><TableCell>{total.bill_count}</TableCell>
      {PAYABLES_BUCKETS.map((b) => <TableCell key={b} className="font-semibold">{money(total[b])}</TableCell>)}
      <TableCell className="font-semibold">{money(total.total_minor)}</TableCell>
    </TableRow></TableFooter>
  </Table>
}
