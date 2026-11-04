import { useNavigate } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import type { CustomerListItem } from "@/api/types"
import { useSession } from "@/app/session-context"
import { DataTable } from "@/components/common/DataTable"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { CustomerDialog } from "../components/CustomerDialog"
import { useCustomers } from "../hooks"

export function CustomersPage() {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const navigate = useNavigate()
  const [q, setQ] = useState("")
  const [creating, setCreating] = useState(false)
  const customers = useCustomers(q)
  const rows = customers.data?.pages.flatMap((p) => p.items)
  const open = (id: string) => void navigate({ to: "/customers/$customerId", params: { customerId: id } })

  const columns = useMemo<ColumnDef<CustomerListItem, unknown>[]>(() => [
    { header: t("crm.fields.name"), cell: ({ row }) => <span className="font-medium">{row.original.display_name}</span> },
    { header: t("crm.fields.contact"), cell: ({ row }) => row.original.primary_email ?? row.original.primary_phone ?? "—" },
    { header: t("crm.fields.tags"), cell: ({ row }) => <span className="flex flex-wrap gap-1">{(row.original.tags ?? []).map((tag) => <Badge key={tag} variant="outline">{tag}</Badge>)}</span> },
    { header: t("crm.fields.lastInteraction"), cell: ({ row }) => row.original.last_interaction_at ? <LocalTime iso={row.original.last_interaction_at} timeZone={businessTimeZone} pattern="d MMM yyyy" /> : "—" },
  ], [t, businessTimeZone])

  return (
    <>
      <PageHeader title={t("nav.customers")} description={t("crm.description")}
        actions={<PermissionGate permission="crm.customer.write"><Button onClick={() => setCreating(true)}>{t("crm.new")}</Button></PermissionGate>} />
      <div className="mb-4 max-w-sm space-y-1">
        <Label htmlFor="customer-search">{t("crm.search")}</Label>
        <Input id="customer-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("crm.searchHint")} />
      </div>
      <DataTable columns={columns} data={rows} isLoading={customers.isPending} error={customers.error} onRetry={() => customers.refetch()}
        hasMore={customers.hasNextPage} onLoadMore={() => customers.fetchNextPage()} rowHref={(c) => `/customers/${c.id}`} onRowClick={(c) => open(c.id)}
        caption={t("nav.customers")} emptyTitle={t("crm.empty")} />
      {creating ? <CustomerDialog open onOpenChange={setCreating} onSaved={open} /> : null}
    </>
  )
}
