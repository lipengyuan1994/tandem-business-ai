import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { CustomerDetail } from "@/api/types"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toastError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"
import { useCreateCustomer, useUpdateCustomer } from "../hooks"

interface Draft {
  display_name: string
  email: string
  phone: string
}

/** Create (no `customer`) or edit (with `customer`) dialog. Edits send the version; a 409 opens the conflict dialog,
 *  and "Reload" refreshes the record while the form keeps what the user typed (the next save uses the new version). */
export function CustomerDialog({ open, onOpenChange, customer, onSaved, onReload }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  customer?: CustomerDetail
  onSaved?: (id: string) => void
  onReload?: () => void
}) {
  const { t } = useTranslation()
  const create = useCreateCustomer()
  const update = useUpdateCustomer(customer?.id ?? "")
  const [draft, setDraft] = useState<Draft>({ display_name: customer?.display_name ?? "", email: "", phone: "" })
  const [initialPreferred] = useState(customer?.preferred_contact_channel ?? "email")
  const [preferred, setPreferred] = useState(initialPreferred)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [conflict, setConflict] = useState(false)
  const pending = create.isPending || update.isPending

  const onError = (e: unknown) => {
    if (e instanceof ApiProblem && e.status === 422) setFieldErrors(e.fieldErrors())
    toastError(e, () => setConflict(true))
  }

  const submit = () => {
    if (!draft.display_name.trim()) return setFieldErrors({ display_name: "required" })
    if (customer) {
      update.mutate({ version: customer.version, display_name: draft.display_name.trim(), ...(preferred !== initialPreferred ? { preferred_contact_channel: preferred } : {}) }, {
        onSuccess: (c) => { onOpenChange(false); onSaved?.(c.id) },
        onError,
      })
      return
    }
    const contact_points = [
      ...(draft.email ? [{ type: "email" as const, value: draft.email, is_primary: true }] : []),
      ...(draft.phone ? [{ type: "phone" as const, value: draft.phone, is_primary: !draft.email }] : []),
    ]
    create.mutate({ kind: "person", display_name: draft.display_name.trim(), preferred_contact_channel: preferred, contact_points, tags: [], consents: [] }, {
      onSuccess: (c) => { onOpenChange(false); onSaved?.(c.id) },
      onError,
    })
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{customer ? t("crm.edit") : t("crm.new")}</DialogTitle>
            <DialogDescription>{t("crm.dialogHint")}</DialogDescription>
          </DialogHeader>
          <form id="customer-form" className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit() }}>
            <div className="space-y-1">
              <Label htmlFor="c-name">{t("crm.fields.name")}</Label>
              <Input id="c-name" value={draft.display_name} aria-invalid={!!fieldErrors.display_name} aria-describedby={fieldErrors.display_name ? "c-name-err" : undefined}
                onChange={(e) => setDraft({ ...draft, display_name: e.target.value })} />
              {fieldErrors.display_name ? <p id="c-name-err" className="text-sm text-destructive">{t("crm.nameRequired")}</p> : null}
            </div>
            <div className="space-y-1">
              <Label htmlFor="c-preferred">{t("crm.preferredContact")}</Label>
              <select id="c-preferred" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={preferred} onChange={(e) => setPreferred(e.target.value as typeof preferred)}>
                {(["email", "web_chat", "none"] as const).map((value) => <option key={value} value={value}>{t(`crm.contactChannels.${value}`)}</option>)}
              </select>
            </div>
            {customer ? null : (
              <>
                <div className="space-y-1">
                  <Label htmlFor="c-email">{t("crm.fields.email")}</Label>
                  <Input id="c-email" type="email" value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="c-phone">{t("crm.fields.phone")}</Label>
                  <Input id="c-phone" type="tel" value={draft.phone} onChange={(e) => setDraft({ ...draft, phone: e.target.value })} />
                </div>
              </>
            )}
          </form>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
            <Button type="submit" form="customer-form" disabled={pending}>{t("common.save")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); onReload?.() }} />
    </>
  )
}
