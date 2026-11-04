import { Link } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { Schemas } from "@/api/types"
import { DataTable } from "@/components/common/DataTable"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { useQuoteRequests } from "./hooks"
import { WRITE } from "./model"
import { RequestQuotesDialog } from "./RequestQuotesDialog"

export function QuoteRequests() {
  const { t } = useTranslation()
  const query = useQuoteRequests()
  const [creating, setCreating] = useState(false)
  const columns: ColumnDef<Schemas["QuoteRequestOut"], unknown>[] = [
    { header: t("purchasing.item"), cell: ({ row: { original: r } }) => <Link className="record-link" to="/purchasing/quote-requests/$id" params={{ id: r.id }}>{r.lines.map((l) => l.name).join(", ")}</Link> },
    { header: t("purchasing.status"), cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { header: t("procurement.answered"), cell: ({ row: { original: r } }) => t("procurement.responseCount", { x: r.suppliers.filter((s) => s.status === "responded" || s.status === "declined").length, y: r.suppliers.length }) },
    { header: t("procurement.reply"), accessorKey: "reply_by" },
  ]
  return <div className="space-y-4"><PermissionGate permission={WRITE}><Button onClick={() => setCreating(true)}>{t("procurement.request")}</Button></PermissionGate>
    <DataTable columns={columns} data={query.data?.pages.flatMap((p) => p.items)} isLoading={query.isPending} error={query.error} onRetry={() => void query.refetch()} hasMore={query.hasNextPage} onLoadMore={() => { if (!query.isFetchingNextPage) void query.fetchNextPage() }} caption={t("procurement.requests")} emptyTitle={t("procurement.empty")} />
    {creating ? <PermissionGate permission={WRITE}><RequestQuotesDialog onClose={() => setCreating(false)} /></PermissionGate> : null}
  </div>
}
