import { QueryError } from "@/components/common/QueryError"
import { useNavigate, useSearch } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { useSession } from "@/app/session-context"
import { EmptyState } from "@/components/common/EmptyState"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { describeError } from "@/lib/errors"
import { formatInZone } from "@/lib/dates"
import { ApiProblem } from "@/lib/problem"
import { useNow } from "@/lib/use-now"
import { AppointmentSheet } from "./components/AppointmentSheet"
import { ResourceDayGrid } from "./components/ResourceDayGrid"
import { type SlotChoice, SlotFinder } from "./components/SlotFinder"
import { addDays, localToday } from "./grid"
import { useAppointment, useBook, useDayAppointments, useResources } from "./hooks"

export function SchedulePage() {
  const { t } = useTranslation()
  const { businessTimeZone: tz } = useSession()
  const search = useSearch({ from: "/app/schedule" })
  const navigate = useNavigate()
  const now = useNow()
  const today = localToday(tz, now)
  const date = search.date ?? today
  const resources = useResources()
  const appointments = useDayAppointments(date, tz)
  const linked = useAppointment(search.appointment)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [booking, setBooking] = useState(false)
  const [bookError, setBookError] = useState<string | null>(null)
  const book = useBook()
  const setDate = (d: string) => void navigate({ to: "/schedule", search: { ...search, date: d } })
  const selected = appointments.data?.find((a) => a.id === selectedId) ?? (search.appointment ? linked.data : undefined)
  const closeSheet = () => { setSelectedId(null); if (search.appointment) void navigate({ to: "/schedule", search: { ...search, appointment: undefined } }) }

  const pick = (c: SlotChoice) => {
    setBookError(null)
    book.mutate({ customer_id: c.customer_id, service_id: c.service_id, resource_id: c.slot.resource_id, starts_at: c.slot.starts_at }, {
      onSuccess: (a) => { setBooking(false); setDate(formatInZone(a.starts_at, tz, "yyyy-MM-dd")); toast.success(t("schedule.booked", { when: c.slot.local_label })) },
      onError: (e) => setBookError(e instanceof ApiProblem && e.code === "scheduling.slot_unavailable" ? t("schedule.slotTaken") : describeError(e)),
    })
  }

  const active = (resources.data ?? []).filter((r) => r.status === "active")
  return (
    <>
      <PageHeader title={t("nav.schedule")} description={t("schedule.description", { zone: tz })}
        actions={<PermissionGate permission="scheduling.appointment.write"><Button onClick={() => { setBookError(null); setBooking(true) }}>{t("schedule.new")}</Button></PermissionGate>} />
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <Button variant="outline" onClick={() => setDate(addDays(date, -1))} aria-label={t("schedule.previousDay")}>‹</Button>
        <div className="space-y-1">
          <Label htmlFor="schedule-date" className="sr-only">{t("schedule.date")}</Label>
          <Input id="schedule-date" type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
        </div>
        <Button variant="outline" onClick={() => setDate(addDays(date, 1))} aria-label={t("schedule.nextDay")}>›</Button>
        {date !== today ? <Button variant="ghost" onClick={() => setDate(today)}>{t("schedule.today")}</Button> : null}
      </div>
      {resources.isPending || appointments.isPending ? <Skeleton className="h-96 w-full" />
        : resources.error ? <QueryError error={resources.error} onRetry={() => resources.refetch()} />
        : appointments.error ? <QueryError error={appointments.error} onRetry={() => appointments.refetch()} />
        : active.length === 0 ? <EmptyState title={t("schedule.noResources")} />
        : <ResourceDayGrid resources={active} appointments={appointments.data ?? []} timeZone={tz} localDate={date} now={now} isToday={date === today} onSelect={(a) => setSelectedId(a.id)} />}
      {linked.error ? <QueryError error={linked.error} onRetry={() => linked.refetch()} /> : null}
      <AppointmentSheet appointment={selected} timeZone={tz} onClose={closeSheet} />
      <Dialog open={booking} onOpenChange={setBooking}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("schedule.new")}</DialogTitle>
            <DialogDescription>{t("schedule.newHint", { zone: tz })}</DialogDescription>
          </DialogHeader>
          <SlotFinder localDate={date} timeZone={tz} onPick={pick} pending={book.isPending} error={bookError} />
        </DialogContent>
      </Dialog>
    </>
  )
}
