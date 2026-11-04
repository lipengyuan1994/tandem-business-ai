// All API times are UTC ISO strings; display and input happen in the business or location time zone.
import { TZDate } from "@date-fns/tz"
import { format } from "date-fns"

export const DEFAULT_PATTERN = "EEE d MMM yyyy, HH:mm"

export function formatInZone(iso: string, timeZone: string, pattern: string = DEFAULT_PATTERN): string {
  return format(new TZDate(new Date(iso).getTime(), timeZone), pattern)
}

/** Short zone name at that instant, e.g. "MST" or "GMT". Falls back to the IANA name. */
export function zoneAbbreviation(iso: string, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone, timeZoneName: "short" }).formatToParts(new Date(iso))
  return parts.find((p) => p.type === "timeZoneName")?.value ?? timeZone
}

/** Local wall-clock date ("2026-11-10") and time ("10:00") in `timeZone` → UTC ISO string with Z. */
export function localToUtcIso(localDate: string, localTime: string, timeZone: string): string {
  const [y, m, d] = localDate.split("-").map(Number)
  const [hh, mm] = localTime.split(":").map(Number)
  if ([y, m, d, hh, mm].some((n) => n === undefined || Number.isNaN(n))) {
    throw new Error(`invalid local date/time: ${localDate} ${localTime}`)
  }
  const local = new TZDate(y!, m! - 1, d!, hh!, mm!, timeZone)
  return new Date(local.getTime()).toISOString()
}
