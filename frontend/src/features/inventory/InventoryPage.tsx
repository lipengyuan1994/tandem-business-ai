import { formatQuantity as qty } from "@/lib/quantity"
import { useNavigate } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import type { StockLevel } from "@/api/types"
import { useSession } from "@/app/session-context"
import { DataTable } from "@/components/common/DataTable"
import { QueryError } from "@/components/common/QueryError"
import { PageHeader } from "@/components/common/PageHeader"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { SuggestionsPanel } from "./components/SuggestionsPanel"
import { useLevels, useSuggestions } from "./hooks"


export function InventoryPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { permissions } = useSession()
  const [belowOnly, setBelowOnly] = useState(false)
  const levels = useLevels(belowOnly)
  const suggestions = useSuggestions(permissions.has("inventory.reorder.read"))

  const columns = useMemo<ColumnDef<StockLevel, unknown>[]>(() => [
    { header: t("inventory.fields.item"), cell: ({ row }) => <span className="font-medium">{row.original.item_name}</span> },
    { header: t("inventory.fields.available"), cell: ({ row }) => (
      <span className={cn("font-medium", row.original.below_reorder && "text-destructive")} data-below={row.original.below_reorder}>
        {qty(row.original.available)}{row.original.below_reorder ? <span className="block text-sm"> {t("inventory.belowReorder")}</span> : null}
      </span>
    ) },
    { header: t("inventory.fields.onHand"), cell: ({ row }) => qty(row.original.on_hand) },
    { header: t("inventory.fields.reserved"), cell: ({ row }) => qty(row.original.reserved) },
    { header: t("inventory.fields.reorderPoint"), cell: ({ row }) => (row.original.reorder_point == null ? "—" : qty(row.original.reorder_point)) },
  ], [t])

  return (
    <>
      <PageHeader title={t("nav.inventory")} description={t("inventory.description")} />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem] [&>*]:min-w-0">
        <div className="space-y-3">
          <span className="flex items-center gap-2">
            <Checkbox id="below-only" checked={belowOnly} onCheckedChange={(v) => setBelowOnly(v === true)} />
            <Label htmlFor="below-only">{t("inventory.belowOnly")}</Label>
          </span>
          <DataTable columns={columns} data={levels.data} isLoading={levels.isPending} error={levels.error} onRetry={() => levels.refetch()}
            rowHref={(l) => `/inventory/${l.item_id}`} onRowClick={(l) => void navigate({ to: "/inventory/$itemId", params: { itemId: l.item_id } })} caption={t("nav.inventory")} />
        </div>
        <aside>{suggestions.error ? <QueryError error={suggestions.error} onRetry={() => suggestions.refetch()} /> : null}<SuggestionsPanel suggestions={suggestions.data} /></aside>
      </div>
    </>
  )
}
