import { QueryError } from "@/components/common/QueryError"
import { formatQuantity } from "@/lib/quantity"
import { Link, useNavigate, useSearch } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { DataTable } from "@/components/common/DataTable"
import { EmptyState } from "@/components/common/EmptyState"
import { MoneyText } from "@/components/common/MoneyText"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { minorToDecimal } from "@/lib/money"
import { QuoteRequests } from "./QuoteRequests"
import { LateOrders } from "./LateOrders"
import { OrderDialog } from "./OrderDialog"
import { SupplierDialog } from "./SupplierDialog"
import { useOrders, usePurchasingPickers, useSuggestions, useSuppliers } from "./hooks"
import { READ, WRITE, type LineDraft, type PO, type Suggestion, type Supplier } from "./model"

export function PurchasingPage() {
  const { t } = useTranslation()
  const search = useSearch({ from: "/app/purchasing" })
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)
  return <PermissionGate permission={READ} fallback={<EmptyState title={t("purchasing.noAccess")} />}>
    <PageHeader title={t("nav.purchasing")} description={t("purchasing.description")} actions={<PermissionGate permission={WRITE}><Button onClick={() => setCreating(true)}>{t("purchasing.newPO")}</Button></PermissionGate>} />
    <Tabs value={search.tab ?? "orders"} onValueChange={(tab) => { if (tab === "orders" || tab === "quotes" || tab === "suppliers" || tab === "suggestions") void navigate({ to: "/purchasing", search: { ...search, tab }, replace: true }) }}><TabsList aria-label={t("nav.purchasing")}>
      {(["orders", "quotes", "suppliers", "suggestions"] as const).map((tab) => <TabsTrigger key={tab} value={tab}>{t(`purchasing.tabs.${tab}`)}</TabsTrigger>)}
    </TabsList>
      <TabsContent value="quotes" className="pt-4"><QuoteRequests /></TabsContent>
      <TabsContent value="orders" className="pt-4"><Orders /></TabsContent>
      <TabsContent value="suppliers" className="space-y-4 pt-4"><Suppliers /></TabsContent>
      <TabsContent value="suggestions" className="pt-4"><Suggestions /></TabsContent>
    </Tabs>
    {creating ? <PermissionGate permission={WRITE}><OrderDialog onClose={() => setCreating(false)} /></PermissionGate> : null}
  </PermissionGate>
}
function Orders() {
  const { t } = useTranslation()
  const list = useOrders()
  const { late } = useSearch({ from: "/app/purchasing" })
  const navigate = useNavigate()
  const columns: ColumnDef<PO, unknown>[] = [
    { header: t("purchasing.number"), cell: ({ row }) => <Link className="record-link" to="/purchasing/purchase-orders/$id" params={{ id: row.original.id }}>{row.original.number ?? t("status.draft")}</Link> },
    { header: t("purchasing.supplier"), accessorKey: "supplier_name" },
    { header: t("purchasing.status"), cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { header: t("purchasing.total"), cell: ({ row }) => <MoneyText value={{ amount_minor: row.original.total_minor, currency: row.original.currency }} /> },
  ]
  return <div className="space-y-4"><label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={!!late} onChange={(e) => { void navigate({ to: "/purchasing", search: { late: e.target.checked || undefined }, replace: true }) }} />{t("procurement.lateOnly")}</label>{late ? <LateOrders /> : <DataTable columns={columns} data={list.data?.pages.flatMap((p) => p.items)} isLoading={list.isPending} error={list.error} onRetry={() => void list.refetch()} hasMore={list.hasNextPage} onLoadMore={() => { if (!list.isFetchingNextPage) void list.fetchNextPage() }} caption={t("purchasing.tabs.orders")} emptyTitle={t("purchasing.emptyOrders")} />}</div>
}
function Suppliers() {
  const { t } = useTranslation()
  const list = useSuppliers()
  const pickers = usePurchasingPickers()
  const [editing, setEditing] = useState<Supplier | "new" | null>(null)
  const columns: ColumnDef<Supplier, unknown>[] = [
    { header: t("purchasing.name"), accessorKey: "name" }, { header: t("purchasing.email"), accessorKey: "email" }, { header: t("purchasing.phone"), accessorKey: "phone" },
    { id: "edit", header: t("purchasing.actions"), cell: ({ row }) => <PermissionGate permission={WRITE}><Button variant="outline" disabled={!pickers.data} onClick={() => setEditing(row.original)}>{t("purchasing.edit")}</Button></PermissionGate> },
  ]
  return <><PermissionGate permission={WRITE}><Button disabled={!pickers.data} onClick={() => setEditing("new")}>{t("purchasing.newSupplier")}</Button></PermissionGate>
    {pickers.error ? <QueryError error={pickers.error} onRetry={() => pickers.refetch()} /> : null}
    <DataTable columns={columns} data={list.data} isLoading={list.isPending} error={list.error} onRetry={() => void list.refetch()} caption={t("purchasing.tabs.suppliers")} emptyTitle={t("purchasing.emptySuppliers")} />
    {editing && pickers.data ? <PermissionGate permission={WRITE}><SupplierDialog supplier={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} /></PermissionGate> : null}
  </>
}
function Suggestions() {
  const { t } = useTranslation()
  const list = useSuggestions()
  const pickers = usePurchasingPickers()
  const [initial, setInitial] = useState<{ supplier: string; lines: LineDraft[] } | null>(null)
  const columns: ColumnDef<Suggestion, unknown>[] = [
    { header: t("purchasing.item"), accessorKey: "item_name" }, { header: t("purchasing.available"), cell: ({ row }) => formatQuantity(row.original.available) },
    { header: t("purchasing.openQuantity"), cell: ({ row }) => formatQuantity(row.original.open_po_quantity) }, { header: t("purchasing.suggested"), cell: ({ row }) => formatQuantity(row.original.suggested) },
    { id: "create", header: t("purchasing.supplier"), cell: ({ row }) => <PermissionGate permission={WRITE}>{row.original.suppliers.length ? <div className="flex flex-wrap gap-2">{row.original.suppliers.map((s) => <Button key={s.id} variant="outline" disabled={Number(row.original.suggested) <= 0 || !pickers.data} onClick={() => {
      const item = (s.items ?? []).find((i) => i.catalog_item_id === row.original.item_id)
      if (item && pickers.data) setInitial({ supplier: s.id, lines: [{ item: row.original.item_id, quantity: row.original.suggested, price: minorToDecimal(item.last_price_minor, pickers.data.currency) }] })
    }}>{t("purchasing.createFor", { supplier: s.name })}</Button>)}</div> : t("purchasing.noSupplier")}</PermissionGate> },
  ]
  return <><DataTable columns={columns} data={list.data} isLoading={list.isPending} error={list.error} onRetry={() => void list.refetch()} caption={t("purchasing.tabs.suggestions")} emptyTitle={t("purchasing.emptySuggestions")} />
    {pickers.error ? <QueryError error={pickers.error} onRetry={() => pickers.refetch()} /> : null}
    {initial ? <PermissionGate permission={WRITE}><OrderDialog initial={initial} onClose={() => setInitial(null)} /></PermissionGate> : null}
  </>
}
