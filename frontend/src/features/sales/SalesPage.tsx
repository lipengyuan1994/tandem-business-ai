import { Link } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import type { SalesDocument } from "@/api/types"
import { useSession } from "@/app/session-context"
import { DataTable } from "@/components/common/DataTable"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { MoneyText } from "@/components/common/MoneyText"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CreateDocumentDialog } from "./components/CreateDocumentDialog"
import { ReminderDialog } from "./components/ReminderDialog"
import { useSalesDocuments } from "./hooks"
import { DOCUMENT_ROUTES, FAMILIES, READ, STATUSES, WRITE, type DocumentFamily } from "./model"

export function SalesPage() {
  const { t } = useTranslation()
  const [creating, setCreating] = useState<"quotes" | "invoices" | null>(null)
  return <PermissionGate permission={READ} fallback={<EmptyState title={t("sales.noAccess")} />}>
    <PageHeader title={t("nav.sales")} description={t("sales.description")} actions={<PermissionGate permission={WRITE}>
      <Button onClick={() => setCreating("quotes")}>{t("sales.new.quotes")}</Button><Button variant="outline" onClick={() => setCreating("invoices")}>{t("sales.new.invoices")}</Button>
    </PermissionGate>} />
    <Tabs defaultValue="quotes">
      <TabsList aria-label={t("sales.documentTabs")}>{FAMILIES.map((f) => <TabsTrigger key={f} value={f}>{t(`sales.tabs.${f}`)}</TabsTrigger>)}</TabsList>
      {FAMILIES.map((f) => <TabsContent key={f} value={f} className="space-y-4 pt-4"><DocumentList family={f} />{f === "invoices" ? <InvoiceExport /> : null}</TabsContent>)}
    </Tabs>
    {creating ? <PermissionGate permission={WRITE}><CreateDocumentDialog family={creating} onOpenChange={(open) => { if (!open) setCreating(null) }} /></PermissionGate> : null}
  </PermissionGate>
}
function DocumentList({ family }: { family: DocumentFamily }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const [status, setStatus] = useState("")
  const [reminding, setReminding] = useState<SalesDocument | null>(null)
  const list = useSalesDocuments(family, status)
  const columns = useMemo<ColumnDef<SalesDocument, unknown>[]>(() => [
    { header: t("sales.fields.number"), cell: ({ row: { original: d } }) => <Link className="record-link" to={DOCUMENT_ROUTES[family]} params={{ id: d.id }}>{d.number ?? t("status.draft")}</Link> },
    { header: t("sales.fields.customer"), accessorKey: "bill_to_name" },
    { header: t("sales.fields.date"), cell: ({ row: { original: d } }) => d.issue_date ?? <LocalTime iso={d.created_at} timeZone={businessTimeZone} pattern="d MMM yyyy" /> },
    { header: t("sales.fields.status"), cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { header: t("sales.fields.total"), cell: ({ row }) => <MoneyText value={{ amount_minor: row.original.total_minor, currency: row.original.currency }} /> },
    ...(family === "invoices" && status === "overdue" ? [{ header: t("sales.reminder.title"), cell: ({ row }: { row: { original: SalesDocument } }) => <PermissionGate permission={WRITE}><Button variant="outline" disabled={!row.original.bill_to_email} onClick={() => setReminding(row.original)}>{t("sales.reminder.title")}</Button></PermissionGate> }] : []),
  ], [t, family, businessTimeZone, status])
  return <>
    <div className="max-w-xs space-y-1"><Label htmlFor={`${family}-status`}>{t("sales.fields.status")}</Label>
      <select id={`${family}-status`} className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">{t("sales.allStatuses")}</option>{STATUSES[family].map((s) => <option key={s} value={s}>{t(`status.${s}`)}</option>)}
        {family === "invoices" ? <option value="overdue">{t("sales.overdue")}</option> : null}
      </select>
    </div>
    <DataTable columns={columns} data={list.data?.pages.flatMap((p) => p.items)} isLoading={list.isPending} error={list.error} onRetry={() => void list.refetch()}
      hasMore={list.hasNextPage} onLoadMore={() => { if (!list.isFetchingNextPage) void list.fetchNextPage() }} caption={t(`sales.tabs.${family}`)} emptyTitle={t("sales.empty")} />
    {reminding ? <PermissionGate permission={WRITE}><ReminderDialog document={reminding} onClose={() => setReminding(null)} /></PermissionGate> : null}
  </>
}
function InvoiceExport() {
  const { t } = useTranslation()
  const [from, setFrom] = useState("")
  const [to, setTo] = useState("")
  const ready = /^\d{4}-\d{2}-\d{2}$/.test(from) && /^\d{4}-\d{2}-\d{2}$/.test(to) && from <= to
  return <section className="space-y-2 rounded-md border p-4" aria-label={t("sales.export")}>
    <p className="text-sm text-muted-foreground">{t("sales.exportHint")}</p>
    <div className="flex flex-wrap items-end gap-3">
      <div className="space-y-1"><Label htmlFor="sales-from">{t("sales.fromDate")}</Label><Input id="sales-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
      <div className="space-y-1"><Label htmlFor="sales-to">{t("sales.toDate")}</Label><Input id="sales-to" type="date" value={to} min={from || undefined} aria-invalid={!!from && !!to && to < from} onChange={(e) => setTo(e.target.value)} /></div>
      {ready ? <Button asChild variant="outline"><a href={`/api/v1/sales/invoices/export.csv?from=${from}&to=${to}`} download>{t("sales.export")}</a></Button> : <Button disabled variant="outline">{t("sales.export")}</Button>}
    </div>
    {from && to && to < from ? <p role="alert" className="text-sm text-destructive">{t("sales.invalidRange")}</p> : null}
  </section>
}
