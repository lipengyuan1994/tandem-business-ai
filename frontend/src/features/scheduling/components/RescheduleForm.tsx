import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { Appointment } from "@/api/types"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { formatInZone } from "@/lib/dates"
import { describeError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"
import { useReschedule, useSlots } from "../hooks"

/** Move a confirmed appointment: pick a day, then one of the server's free slots for the same service. */
export function RescheduleForm({ appointment, timeZone, onMoved, onConflict }: { appointment: Appointment; timeZone: string; onMoved: () => void; onConflict: () => void }) {
  const { t } = useTranslation()
  const [date, setDate] = useState(formatInZone(appointment.starts_at, timeZone, "yyyy-MM-dd"))
  const [error, setError] = useState<string | null>(null)
  const slots = useSlots(appointment.service_id, date, timeZone)
  const move = useReschedule()
  const free = (slots.data ?? []).filter((s) => s.starts_at !== appointment.starts_at || s.resource_id !== appointment.resource_id)

  const pick = (starts_at: string, resource_id: string, label: string) => {
    setError(null)
    move.mutate({ id: appointment.id, version: appointment.version, starts_at, resource_id }, {
      onSuccess: () => { toast.success(t("schedule.moved", { when: label })); onMoved() },
      onError: (e) => {
        if (e instanceof ApiProblem && e.isConflict && e.code.endsWith("version_conflict")) onConflict()
        else setError(e instanceof ApiProblem && e.code === "scheduling.slot_unavailable" ? t("schedule.slotTaken") : describeError(e))
      },
    })
  }

  return (
    <section className="space-y-2" aria-labelledby="reschedule-title">
      <h3 id="reschedule-title" className="font-medium">{t("schedule.reschedule.title")}</h3>
      <div className="space-y-1">
        <Label htmlFor="reschedule-date">{t("schedule.reschedule.date")}</Label>
        <Input id="reschedule-date" type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
      </div>
      {slots.error ? <QueryError error={slots.error} onRetry={() => slots.refetch()} />
        : slots.isPending ? <p className="text-muted-foreground">{t("common.loading")}</p>
        : free.length === 0 ? <p className="text-muted-foreground">{t("schedule.noSlots")}</p>
        : (
          <ul className="flex flex-wrap gap-2" aria-label={t("schedule.freeSlots")}>
            {free.map((s) => (
              <li key={`${s.resource_id}-${s.starts_at}`}>
                <Button size="sm" variant="outline" disabled={move.isPending} onClick={() => pick(s.starts_at, s.resource_id, s.local_label)}>
                  {`${s.local_label} · ${s.resource_name}`}
                </Button>
              </li>
            ))}
          </ul>
        )}
      {error ? <p role="alert" className="text-destructive">{error}</p> : null}
    </section>
  )
}
