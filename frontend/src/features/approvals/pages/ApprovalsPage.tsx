import { readableAction } from "@/lib/action-label"
import { useNavigate } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import type { Proposal } from "@/api/types"
import { useSession } from "@/app/session-context"
import { AiBadge } from "@/features/ai/AiBadge"
import { DataTable } from "@/components/common/DataTable"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { type QueueTab, useProposals } from "../hooks"

export function ApprovalsPage() {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const navigate = useNavigate()
  const [tab, setTab] = useState<QueueTab>("mine-to-decide")
  const proposals = useProposals(tab)

  const columns = useMemo<ColumnDef<Proposal, unknown>[]>(() => [
    {
      header: t("approvals.columns.title"),
      cell: ({ row }) => (
        <span className="flex items-center gap-2">
          <AiBadge originKind={row.original.origin_kind} />
          <span>{row.original.title}<span className="ml-2 text-sm">{t("approvals.review")}</span></span>
        </span>
      ),
    },
    { header: t("approvals.columns.type"), cell: ({ row }) => <span>{t(`approvals.actionLabels.${row.original.action_type}`, { defaultValue: readableAction(row.original.action_type) })}</span> },
    { header: t("approvals.columns.status"), cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { header: t("approvals.columns.created"), cell: ({ row }) => <LocalTime iso={row.original.created_at} timeZone={businessTimeZone} pattern="d MMM HH:mm" /> },
  ], [t, businessTimeZone])

  return (
    <>
      <PageHeader title={t("nav.approvals")} description={t("approvals.description")} />
      <Tabs value={tab} onValueChange={(v) => setTab(v as QueueTab)} className="mb-4">
        <TabsList>
          <TabsTrigger value="mine-to-decide">{t("approvals.tabs.mine")}</TabsTrigger>
          <TabsTrigger value="pending">{t("approvals.tabs.pending")}</TabsTrigger>
          <TabsTrigger value="recent">{t("approvals.tabs.recent")}</TabsTrigger>
        </TabsList>
        <TabsContent value={tab}>
      <DataTable
        columns={columns}
        data={proposals.data?.items}
        isLoading={proposals.isPending}
        error={proposals.error}
        onRetry={() => proposals.refetch()}
        emptyTitle={t("approvals.empty")}
        caption={t("nav.approvals")}
        rowHref={(p) => `/approvals/${p.id}`}
        onRowClick={(p) => navigate({ to: "/approvals/$proposalId", params: { proposalId: p.id } })}
      />
        </TabsContent>
      </Tabs>
    </>
  )
}
