import type { ColumnDef } from "@tanstack/react-table"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import type { Member } from "@/api/types"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { DataTable } from "@/components/common/DataTable"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { toastError } from "@/lib/errors"
import { InviteDialog } from "../components/InviteDialog"
import { RolesDialog } from "../components/RolesDialog"
import { useMembers, useUpdateMember } from "../hooks"

export function StaffPage() {
  const { t } = useTranslation()
  const members = useMembers()
  const update = useUpdateMember()
  const [inviting, setInviting] = useState(false)
  const [editing, setEditing] = useState<Member | null>(null)
  const [suspending, setSuspending] = useState<Member | null>(null)

  const columns = useMemo<ColumnDef<Member, unknown>[]>(() => [
    { header: t("identity.staff.name"), accessorKey: "display_name" },
    { header: t("identity.staff.email"), accessorKey: "email" },
    { header: t("identity.staff.roles"), cell: ({ row }) => row.original.roles.map((r) => t(`roles.${r}`)).join(", ") },
    { header: t("identity.staff.status"), cell: ({ row }) => <StatusBadge status={row.original.status === "active" ? "confirmed" : "cancelled"} /> },
    {
      id: "actions",
      header: () => <span className="sr-only">{t("common.actions")}</span>,
      cell: ({ row }) => (
        <PermissionGate permission="identity.membership.write">
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" onClick={() => setEditing(row.original)}>{t("identity.staff.editRoles")}</Button>
            {row.original.status === "active" ? (
              <Button size="sm" variant="ghost" onClick={() => setSuspending(row.original)}>{t("identity.staff.suspend")}</Button>
            ) : null}
          </div>
        </PermissionGate>
      ),
    },
  ], [t])

  return (
    <>
      <PageHeader
        title={t("identity.staff.title")}
        description={t("identity.staff.description")}
        actions={<PermissionGate permission="identity.membership.write"><Button onClick={() => setInviting(true)}>{t("identity.staff.invite")}</Button></PermissionGate>}
      />
      <DataTable columns={columns} data={members.data} isLoading={members.isPending} error={members.error} onRetry={() => members.refetch()} caption={t("identity.staff.title")} />
      <InviteDialog open={inviting} onOpenChange={setInviting} />
      <RolesDialog member={editing} onClose={() => setEditing(null)} />
      <ConfirmDialog
        open={suspending !== null}
        onOpenChange={(open) => !open && setSuspending(null)}
        title={t("identity.staff.suspendTitle", { name: suspending?.display_name })}
        description={t("identity.staff.suspendHelp")}
        destructive
        pending={update.isPending}
        confirmLabel={t("identity.staff.suspend")}
        onConfirm={() =>
          suspending &&
          update.mutate({ membershipId: suspending.membership_id, version: suspending.version, status: "suspended" },
            { onSuccess: () => setSuspending(null), onError: (e) => toastError(e) })
        }
      />
    </>
  )
}
