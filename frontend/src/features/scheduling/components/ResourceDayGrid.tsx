import { useTranslation } from "react-i18next"
import type { Appointment, Resource } from "@/api/types"
import { formatInZone, localToUtcIso, zoneAbbreviation } from "@/lib/dates"
import { cn } from "@/lib/utils"
import { SLOT_MINUTES, minutesLeft, place, visibleWindow, wallMinutes } from "../grid"

const ROW_REM = 1.5
const VISIBLE = new Set(["held", "confirmed", "completed", "no_show"])
const TONE: Record<string, string> = {
  confirmed: "border-status-approved bg-status-approved/15",
  completed: "border-status-success bg-status-success/15",
  no_show: "border-status-failed bg-status-failed/10",
  held: "border-status-pending border-dashed hatched",
}

/** Resources as columns, 15-minute rows in the business time zone; holds hatched with their expiry countdown (M06 UI). */
export function ResourceDayGrid({ resources, appointments, timeZone, localDate, now, isToday, onSelect }: {
  resources: Resource[]
  appointments: Appointment[]
  timeZone: string
  /** The local day shown ("2026-11-10"); its zone abbreviation labels the grid (MST vs MDT). */
  localDate: string
  now: Date
  isToday: boolean
  onSelect: (a: Appointment) => void
}) {
  const { t } = useTranslation()
  const shown = appointments.filter((a) => VISIBLE.has(a.status))
  const window = visibleWindow(shown, timeZone)
  const rows = (window.endMinute - window.startMinute) / SLOT_MINUTES
  const nowMinute = wallMinutes(now.toISOString(), timeZone)
  const zone = zoneAbbreviation(localToUtcIso(localDate, "12:00", timeZone), timeZone)
  const hours = Array.from({ length: rows / 4 }, (_, i) => window.startMinute / 60 + i)

  return (
    <div className="min-w-0">
      {!shown.length ? <p role="status" className="mb-3 text-sm">{t("schedule.emptyDay")}</p> : null}
      <p className="mb-2 text-sm text-muted-foreground md:hidden">{t("schedule.scrollHint")}</p>
    {/* Keyboard focus enables native scrolling of the timetable. */}
    {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
    <div role="region" aria-label={t("schedule.timetable")} tabIndex={0} className="max-h-[65dvh] overflow-auto rounded-lg border focus-visible:outline-2 focus-visible:outline-ring">
      <div className="grid min-w-fit" style={{ gridTemplateColumns: `4rem repeat(${resources.length}, minmax(10rem, 1fr))` }}>
        <div className="sticky left-0 top-0 z-30 border-b bg-card px-2 py-2 text-sm text-muted-foreground">{zone}</div>
        {resources.map((r) => <div key={r.id} aria-hidden="true" className="sticky top-0 z-20 border-b border-l bg-card px-2 py-2 text-sm font-medium">{r.name}</div>)}

        <div className="relative sticky left-0 z-10 bg-card" style={{ height: `${rows * ROW_REM}rem` }} aria-hidden="true">
          {hours.map((h, i) => (
            <span key={h} className="absolute right-2 -translate-y-1/2 text-sm text-muted-foreground" style={{ top: `${i * 4 * ROW_REM}rem` }}>
              {i === 0 ? "" : `${String(h).padStart(2, "0")}:00`}
            </span>
          ))}
        </div>
        {resources.map((r) => (
          <section key={r.id} aria-label={r.name} className="relative border-l" style={{ height: `${rows * ROW_REM}rem`,
            backgroundImage: `repeating-linear-gradient(to bottom, transparent 0, transparent calc(${4 * ROW_REM}rem - 1px), var(--border) calc(${4 * ROW_REM}rem - 1px), var(--border) ${4 * ROW_REM}rem)` }}>
            {isToday && nowMinute >= window.startMinute && nowMinute < window.endMinute ? (
              <div data-testid="now-line" className="pointer-events-none absolute inset-x-0 z-20 h-0.5 bg-destructive" style={{ top: `${((nowMinute - window.startMinute) / SLOT_MINUTES) * ROW_REM}rem` }}><span className="absolute right-0 -top-5 rounded bg-background px-1 text-sm text-destructive">{t("schedule.currentTime")}</span></div>
            ) : null}
            {shown.filter((a) => a.resource_id === r.id).map((a) => {
              const pos = place(a.starts_at, a.ends_at, timeZone, window)
              const time = `${formatInZone(a.starts_at, timeZone, "HH:mm")}–${formatInZone(a.ends_at, timeZone, "HH:mm")}`
              const aZone = zoneAbbreviation(a.starts_at, timeZone) // differs from the header only on DST days
              const left = a.status === "held" ? minutesLeft(a.hold_expires_at, now) : null
              const status = t(`status.${a.status}`)
              return (
                <button key={a.id} type="button" onClick={() => onSelect(a)} data-status={a.status}
                  aria-label={`${time} ${aZone}, ${a.service_name}, ${a.customer_name}, ${status}${left !== null ? `, ${t("schedule.holdExpires", { n: left })}` : ""}`}
                  className={cn("absolute inset-x-1 overflow-hidden rounded-md border-l-4 px-2 py-1 text-left text-sm shadow-sm focus-visible:outline-2 focus-visible:outline-ring", TONE[a.status])}
                  style={{ top: `${pos.top * ROW_REM}rem`, height: `${pos.height * ROW_REM}rem` }}>
                  <span className="block font-medium">{time} {a.service_name}</span>
                  <span className="block truncate">{a.customer_name}</span>
                  {left !== null ? <span className="block font-medium">{t("schedule.holdExpires", { n: left })}</span> : null}
                </button>
              )
            })}
          </section>
        ))}
      </div>
    </div>
    </div>
  )
}
