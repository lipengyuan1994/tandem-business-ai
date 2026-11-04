import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { formatInZone } from "@/lib/dates"
import { SlotFinder } from "@/features/scheduling/components/SlotFinder"
import { localToday } from "@/features/scheduling/grid"
import type { ActionFormProps } from "./types"

interface BookingPayload {
  customer?: { id?: string; new?: { display_name?: string } }
  service_id?: string
  starts_at?: string
}

/** scheduling.book_appointment: pick another free slot; customer, reply and evidence stay as proposed. */
export function BookingForm({ payload, pending, onSubmit, onCancel }: ActionFormProps) {
  const { t } = useTranslation()
  const { businessTimeZone: tz } = useSession()
  const p = payload as BookingPayload
  const date = p.starts_at ? formatInZone(p.starts_at, tz, "yyyy-MM-dd") : localToday(tz)
  const customerName = p.customer?.new?.display_name ?? t("approvals.forms.existingCustomer")
  return (
    <div className="space-y-3">
      <SlotFinder localDate={date} timeZone={tz} pending={pending} lockCustomer
        initial={{ service_id: p.service_id, customer_id: p.customer?.id ?? "new", customer_name: customerName }}
        onPick={(c) => onSubmit({ ...payload, service_id: c.service_id, resource_id: c.slot.resource_id, starts_at: c.slot.starts_at, hold_id: null })} />
      <p className="text-xs text-muted-foreground">{t("approvals.forms.bookingNote")}</p>
      <Button type="button" variant="outline" onClick={onCancel}>{t("common.cancel")}</Button>
    </div>
  )
}
