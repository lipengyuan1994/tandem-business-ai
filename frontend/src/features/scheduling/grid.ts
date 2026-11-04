// Pure helpers for the day grid. Everything is computed in the business time zone, never the browser's.
import { formatInZone, localToUtcIso } from "@/lib/dates"

export const SLOT_MINUTES = 15
export const DEFAULT_DAY = { startMinute: 7 * 60, endMinute: 19 * 60 }

/** Today's local date ("2026-11-10") in `timeZone`. */
export function localToday(timeZone: string, now: Date = new Date()): string {
  return formatInZone(now.toISOString(), timeZone, "yyyy-MM-dd")
}

/** Shift a local date by whole days (calendar arithmetic, no time zone involved). */
export function addDays(localDate: string, days: number): string {
  const d = new Date(`${localDate}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

/** UTC bounds of a local calendar day; correct on DST days (23 or 25 hours long). */
export function dayBoundsUtc(localDate: string, timeZone: string): { start: string; end: string } {
  return { start: localToUtcIso(localDate, "00:00", timeZone), end: localToUtcIso(addDays(localDate, 1), "00:00", timeZone) }
}

/** Minutes since local midnight shown on the wall clock in `timeZone`. */
export function wallMinutes(iso: string, timeZone: string): number {
  const [h, m] = formatInZone(iso, timeZone, "HH:mm").split(":").map(Number)
  return (h ?? 0) * 60 + (m ?? 0)
}

export interface Placed {
  top: number
  height: number
}

/** Grid position in 15-minute rows, clipped to the visible window. */
export function place(startsAt: string, endsAt: string, timeZone: string, window = DEFAULT_DAY): Placed {
  const start = Math.max(wallMinutes(startsAt, timeZone), window.startMinute)
  let end = wallMinutes(endsAt, timeZone)
  if (end <= start) end = window.endMinute // runs past midnight
  end = Math.min(end, window.endMinute)
  return { top: (start - window.startMinute) / SLOT_MINUTES, height: Math.max(1, (end - start) / SLOT_MINUTES) }
}

/** Visible window: the default working day, widened to include every appointment. */
export function visibleWindow(items: { starts_at: string; ends_at: string }[], timeZone: string): typeof DEFAULT_DAY {
  let { startMinute, endMinute } = DEFAULT_DAY
  for (const a of items) {
    startMinute = Math.min(startMinute, Math.floor(wallMinutes(a.starts_at, timeZone) / 60) * 60)
    const end = wallMinutes(a.ends_at, timeZone)
    if (end > 0) endMinute = Math.max(endMinute, Math.ceil(end / 60) * 60)
  }
  return { startMinute, endMinute }
}

/** Whole minutes until a hold expires (0 when already expired). */
export function minutesLeft(expiresAt: string | null, now: Date = new Date()): number {
  if (!expiresAt) return 0
  return Math.max(0, Math.ceil((new Date(expiresAt).getTime() - now.getTime()) / 60_000))
}
