import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { Member, Role } from "@/api/types"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { toastError } from "@/lib/errors"
import { ROLES, useSetRoles } from "../hooks"

export function RolesDialog({ member, onClose }: { member: Member | null; onClose: () => void }) {
  return (
    <Dialog open={member !== null} onOpenChange={(open) => !open && onClose()}>
      {member ? <RolesBody key={member.membership_id} member={member} onClose={onClose} /> : null}
    </Dialog>
  )
}

function RolesBody({ member, onClose }: { member: Member; onClose: () => void }) {
  const { t } = useTranslation()
  const session = useSession()
  const setRoles = useSetRoles()
  const [roles, setLocal] = useState<Role[]>(member.roles)
  const canOwner = session.permissions.has("identity.owner.grant")

  return (
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("identity.roles.title", { name: member.display_name })}</DialogTitle>
          <DialogDescription>{t("identity.roles.description")}</DialogDescription>
        </DialogHeader>
        <fieldset className="space-y-2">
          <legend className="sr-only">{t("identity.invite.roles")}</legend>
          {ROLES.map((role) => (
            <div key={role} className="flex items-center gap-2">
              <Checkbox
                id={`edit-role-${role}`}
                checked={roles.includes(role)}
                disabled={role === "owner" && !canOwner}
                onCheckedChange={(checked) => setLocal(checked ? [...roles, role] : roles.filter((r) => r !== role))}
              />
              <Label htmlFor={`edit-role-${role}`} className="font-normal">{t(`roles.${role}`)}</Label>
            </div>
          ))}
        </fieldset>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t("common.cancel")}</Button>
          <Button
            disabled={roles.length === 0 || setRoles.isPending}
            onClick={() => setRoles.mutate({ membershipId: member.membership_id, roles }, { onSuccess: onClose, onError: (e) => toastError(e) })}
          >
            {t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
  )
}
