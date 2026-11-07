import { useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { Appointment } from "@/api/types"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { LocalTime } from "@/components/common/LocalTime"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { toastError } from "@/lib/errors"
import { useInvoiceFromAppointment } from "@/features/sales/hooks"
import { type AppointmentAction, useAppointmentAction } from "../hooks"

export function AppointmentSheet({ appointment, timeZone, onClose }: { appointment: Appointment | undefined; timeZone: string; onClose: () => void }) {
  const { t } = useTranslation()
  const act = useAppointmentAction()
  const invoice = useInvoiceFromAppointment()
  const navigate = useNavigate()
  const [reason, setReason] = useState("")
  const [conflict, setConflict] = useState(false)
  const a = appointment
  const run = (action: AppointmentAction) => a && act.mutate({ id: a.id, action, version: a.version, reason }, { onSuccess: onClose, onError: (e) => toastError(e, () => setConflict(true)) })

  return (
    <>
      <Sheet open={!!a} onOpenChange={(open) => !open && onClose()}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{a ? `${a.service_name} · ${a.customer_name}` : ""}</SheetTitle>
            <SheetDescription>{a ? <LocalTime iso={a.starts_at} timeZone={timeZone} pattern="EEE d MMM yyyy, HH:mm zzz" /> : null}</SheetDescription>
          </SheetHeader>
          {a ? (
            <div className="space-y-4 px-4 text-sm">
              <p className="flex items-center gap-2"><StatusBadge status={a.status} /> {a.resources?.length ? a.resources.map((r) => r.resource_name).join(" + ") : a.resource_name}</p>
              {a.notes ? <p className="whitespace-pre-wrap">{a.notes}</p> : null}
              {a.proposal_id ? <p><a className="underline" href={`/approvals/${a.proposal_id}`}>{t("schedule.viaProposal")}</a></p> : null}
              {a.status === "completed" ? <PermissionGate permission="sales.document.write">
                <p>{t("sales.appointmentHint")}</p>
                <Button disabled={invoice.isPending} onClick={() => invoice.mutate(a.id, {
                  onSuccess: (d) => { toast.success(t("sales.created")); onClose(); void navigate({ to: "/sales/invoices/$id", params: { id: d.id } }) },
                  onError: (err) => toastError(err),
                })}>{t("sales.actions.invoice")}</Button>
              </PermissionGate> : null}
              {a.status === "confirmed" ? (
                <PermissionGate permission="scheduling.appointment.write">
                  <div className="space-y-3">
                    <div className="flex gap-2">
                      <Button onClick={() => run("complete")} disabled={act.isPending}>{t("schedule.actions.complete")}</Button>
                      <Button variant="outline" onClick={() => run("no-show")} disabled={act.isPending}>{t("schedule.actions.noShow")}</Button>
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="cancel-reason">{t("schedule.cancelReason")}</Label>
                      <Input id="cancel-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
                    </div>
                    <Button variant="destructive" onClick={() => run("cancel")} disabled={act.isPending}>{t("schedule.actions.cancel")}</Button>
                  </div>
                </PermissionGate>
              ) : null}
            </div>
          ) : null}
        </SheetContent>
      </Sheet>
      <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); onClose() }} />
    </>
  )
}
