import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { Schemas } from "@/api/types"
import { useSession } from "@/app/session-context"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { LocalTime } from "@/components/common/LocalTime"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { toastError } from "@/lib/errors"
import { usePendingInvitations, useRevokeInvitation } from "../hooks"

type Invitation = Schemas["InvitationOut"]

/** Invitations nobody has accepted yet. Revoking one stops that sign-up from joining this business. */
export function PendingInvitations() {
  const { t } = useTranslation()
  const { permissions, businessTimeZone } = useSession()
  const invitations = usePendingInvitations(permissions.has("identity.membership.read"))
  const revoke = useRevokeInvitation()
  const canRevoke = permissions.has("identity.membership.write")
  const [revoking, setRevoking] = useState<Invitation | null>(null)
  if (invitations.error) return <QueryError error={invitations.error} onRetry={() => invitations.refetch()} />
  if (!invitations.data?.length) return null
  return (
    <Card className="mt-6">
      <CardHeader><CardTitle className="text-base">{t("identity.invitations.title")}</CardTitle></CardHeader>
      <CardContent>
        <ul className="divide-y" aria-label={t("identity.invitations.title")}>
          {invitations.data.map((i) => (
            <li key={i.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
              <span className="font-medium">{i.display_name}</span>
              <span className="text-muted-foreground">{i.email}</span>
              <span className="text-muted-foreground">· {i.roles.map((r) => t(`roles.${r}`)).join(", ")}</span>
              <span className="text-muted-foreground">· {t("identity.invitations.expires")} <LocalTime iso={i.expires_at} timeZone={businessTimeZone} pattern="d MMM yyyy" /></span>
              {canRevoke ? <Button size="sm" variant="ghost" className="ml-auto" aria-label={t("identity.invitations.revokeFor", { name: i.display_name })} onClick={() => setRevoking(i)}>{t("identity.invitations.revoke")}</Button> : null}
            </li>
          ))}
        </ul>
      </CardContent>
      <ConfirmDialog open={revoking !== null} onOpenChange={(open) => !open && setRevoking(null)} destructive pending={revoke.isPending}
        title={t("identity.invitations.revokeTitle", { name: revoking?.display_name })} description={t("identity.invitations.revokeHelp")} confirmLabel={t("identity.invitations.revoke")}
        onConfirm={() => revoking && revoke.mutate(revoking.id, {
          onSuccess: () => { toast.success(t("identity.invitations.revoked", { name: revoking.display_name })); setRevoking(null) },
          onError: (e) => { setRevoking(null); toastError(e) },
        })} />
    </Card>
  )
}
