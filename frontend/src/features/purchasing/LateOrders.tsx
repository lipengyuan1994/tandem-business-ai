import { Link } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { Schemas } from "@/api/types"
import { useSession } from "@/app/session-context"
import { DataTable } from "@/components/common/DataTable"
import { LocalTime } from "@/components/common/LocalTime"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Button } from "@/components/ui/button"
import { useLateOrders } from "./hooks"
import { WRITE } from "./model"
import { FollowupDialog } from "./ProcurementDialogs"
export function LateOrders() {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const query = useLateOrders()
  const [follow, setFollow] = useState<Schemas["LatePO"] | null>(null)
  return <><DataTable<Schemas["LatePO"]> data={query.data} isLoading={query.isPending} error={query.error} onRetry={() => void query.refetch()} caption={t("procurement.lateOnly")} emptyTitle={t("procurement.noLate")} columns={[
    { header: t("purchasing.number"), cell: ({ row }) => <Link className="record-link" to="/purchasing/purchase-orders/$id" params={{ id: row.original.id }}>{row.original.number}</Link> },
    { header: t("purchasing.supplier"), accessorKey: "supplier_name" },
    { header: t("procurement.expected"), accessorKey: "expected_delivery_date" },
    { header: t("procurement.daysLate"), cell: ({ row }) => t("procurement.lateDays", { n: row.original.days_late }) },
    { header: t("purchasing.lines"), cell: ({ row }) => row.original.open_lines.map((l) => `${l.name} × ${l.quantity}`).join(", ") },
    { header: t("procurement.lastFollowup"), cell: ({ row }) => row.original.last_followup_at ? <LocalTime iso={row.original.last_followup_at} timeZone={businessTimeZone} /> : t("procurement.unspecified") },
    { header: t("purchasing.actions"), id: "followup", cell: ({ row }) => <PermissionGate permission={WRITE}><Button variant="outline" onClick={() => setFollow(row.original)}>{t("procurement.followup")}</Button></PermissionGate> },
  ]} />{follow ? <PermissionGate permission={WRITE}><FollowupDialog supplier={follow.supplier_id} about={{ purchase_order_id: follow.id }} onClose={() => setFollow(null)} /></PermissionGate> : null}</>
}
