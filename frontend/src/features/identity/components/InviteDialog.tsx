import { zodResolver } from "@hookform/resolvers/zod"
import { useState } from "react"
import { useForm, useWatch } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"
import type { Invitation, Role } from "@/api/types"
import { useSession } from "@/app/session-context"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toastError } from "@/lib/errors"
import { ROLES, useInvite } from "../hooks"

const schema = z.object({
  email: z.email(),
  display_name: z.string().trim().min(1).max(200),
  roles: z.array(z.enum(ROLES as [Role, ...Role[]])).min(1),
})
type FormValues = z.infer<typeof schema>

export function InviteDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const session = useSession()
  const invite = useInvite()
  const [created, setCreated] = useState<Invitation | null>(null)
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { email: "", display_name: "", roles: ["operator"] } })
  const roles = useWatch({ control: form.control, name: "roles" })
  const assignable = ROLES.filter((r) => r !== "owner" || session.permissions.has("identity.owner.grant"))

  const close = (next: boolean) => {
    if (!next) {
      setCreated(null)
      form.reset()
    }
    onOpenChange(next)
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("identity.invite.title")}</DialogTitle>
          <DialogDescription>{t("identity.invite.description")}</DialogDescription>
        </DialogHeader>
        {created ? (
          <div className="space-y-3">
            {created.temporary_password ? (
              <Alert>
                <AlertTitle>{t("identity.invite.passwordTitle")}</AlertTitle>
                <AlertDescription>
                  <p>{t("identity.invite.passwordHelp")}</p>
                  <code className="mt-2 block rounded bg-muted px-2 py-1 font-mono text-sm" aria-label={t("identity.invite.passwordTitle")}>
                    {created.temporary_password}
                  </code>
                </AlertDescription>
              </Alert>
            ) : (
              <p className="text-sm">{t(created.status === "accepted" ? "identity.invite.addedExisting" : "identity.invite.emailSent")}</p>
            )}
            <DialogFooter><Button onClick={() => close(false)}>{t("common.done")}</Button></DialogFooter>
          </div>
        ) : (
          <form
            className="space-y-4"
            onSubmit={form.handleSubmit((values) => invite.mutate(values, { onSuccess: setCreated, onError: (e) => toastError(e) }))}
          >
            <div className="space-y-1">
              <Label htmlFor="invite-email">{t("identity.invite.email")}</Label>
              <Input id="invite-email" type="email" autoComplete="off" aria-invalid={!!form.formState.errors.email} {...form.register("email")} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="invite-name">{t("identity.invite.name")}</Label>
              <Input id="invite-name" aria-invalid={!!form.formState.errors.display_name} {...form.register("display_name")} />
            </div>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">{t("identity.invite.roles")}</legend>
              {assignable.map((role) => (
                <div key={role} className="flex items-center gap-2">
                  <Checkbox
                    id={`role-${role}`}
                    checked={roles.includes(role)}
                    onCheckedChange={(checked) =>
                      form.setValue("roles", checked ? [...roles, role] : roles.filter((r) => r !== role), { shouldValidate: true })
                    }
                  />
                  <Label htmlFor={`role-${role}`} className="font-normal">{t(`roles.${role}`)}</Label>
                </div>
              ))}
            </fieldset>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => close(false)}>{t("common.cancel")}</Button>
              <Button type="submit" disabled={invite.isPending}>{t("identity.invite.submit")}</Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
