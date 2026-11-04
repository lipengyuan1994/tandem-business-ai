import { DEFAULT_PATTERN, formatInZone, zoneAbbreviation } from "@/lib/dates"

/** Renders a UTC instant in a business/location time zone and always shows the zone ("10:00 MST"). */
export function LocalTime({ iso, timeZone, pattern = DEFAULT_PATTERN }: { iso: string; timeZone: string; pattern?: string }) {
  return (
    <time dateTime={iso} title={`${iso} (UTC)`} className="whitespace-nowrap tabular-nums">
      {formatInZone(iso, timeZone, pattern)} {zoneAbbreviation(iso, timeZone)}
    </time>
  )
}
