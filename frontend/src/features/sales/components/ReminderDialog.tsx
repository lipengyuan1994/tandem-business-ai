import { Link } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { SalesDocument } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { describeError } from "@/lib/errors"
import { usePaymentReminder } from "../hooks"

export function ReminderDialog({ document: d, onClose }: { document: SalesDocument; onClose: () => void }) {
  const { t } = useTranslation()
  const [body, setBody] = useState("")
  const reminder = usePaymentReminder(d.id)
  return <Dialog open onOpenChange={(open) => { if (!open && !reminder.isPending) onClose() }}>
    <DialogContent>
      <DialogHeader><DialogTitle>{t("sales.reminder.title")}</DialogTitle><DialogDescription>{t("sales.reminder.hint", { number: d.number, email: d.bill_to_email })}</DialogDescription></DialogHeader>
      {reminder.data ? <Link className="record-link" to="/approvals/$id" params={{ id: reminder.data.id }}>{t("sales.reminder.review")}</Link> : <form id="invoice-reminder" onSubmit={(e) => {
        e.preventDefault()
        if (body.length >= 1 && body.length <= 2000 && !reminder.isPending) reminder.mutate({ body, version: d.version })
      }}>
        <Label htmlFor="reminder-body">{t("sales.reminder.message")}</Label>
        <Textarea id="reminder-body" value={body} minLength={1} maxLength={2000} required disabled={reminder.isPending} onChange={(e) => setBody(e.target.value)} />
        {reminder.error ? <p role="alert" className="text-sm text-destructive">{describeError(reminder.error)}</p> : null}
      </form>}
      <DialogFooter><Button variant="outline" disabled={reminder.isPending} onClick={onClose}>{t(reminder.data ? "common.close" : "common.cancel")}</Button>
        {!reminder.data ? <Button type="submit" form="invoice-reminder" disabled={!body.length || body.length > 2000 || reminder.isPending}>{t("sales.reminder.create")}</Button> : null}
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
