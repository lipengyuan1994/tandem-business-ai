import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { Resource, Schemas } from "@/api/types"
import { useSession } from "@/app/session-context"
import { QueryError } from "@/components/common/QueryError"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { formatInZone, localToUtcIso } from "@/lib/dates"
import { toastError } from "@/lib/errors"
import { useNow } from "@/lib/use-now"
import { useLocations } from "@/features/org/hooks"
import { addDays, localToday } from "../grid"
import { useAddException, useDeleteException, useExceptions } from "../hooks"

type Kind = Schemas["ExceptionIn"]["kind"]
const SELECT = "h-9 w-full rounded-md border bg-background px-3 text-sm"

type Location = { id: string; name: string; timezone: string; is_default: boolean }
/** Who a closure is for: "" everyone at the default location, "all" every location, "loc:<id>" everyone at one, or a resource id. */
type Target = string

function targetBody(target: Target): Pick<Schemas["ExceptionIn"], "resource_id" | "location_id" | "all_locations"> {
  if (target === "all") return { resource_id: null, all_locations: true }
  if (target.startsWith("loc:")) return { resource_id: null, location_id: target.slice(4), all_locations: false }
  return { resource_id: target || null, all_locations: false }
}

/**
 * Times are entered in the zone of the location the closure lands at: the resource's location, the chosen location,
 * or the business's zone for a closure at every location.
 */
function AddClosureForm({ resources, locations, zoneFor, everyoneLabel, businessZone }: { resources: Resource[]; locations: Location[]; zoneFor: (target: Target) => string; everyoneLabel: string; businessZone: string }) {
  const { t } = useTranslation()
  const add = useAddException()
  const [kind, setKind] = useState<Kind>("closed")
  const [resourceId, setResourceId] = useState<Target>("")
  const timeZone = zoneFor(resourceId)
  const today = localToday(timeZone, useNow())
  const [allDay, setAllDay] = useState(true)
  const [fromDate, setFromDate] = useState(today)
  const [toDate, setToDate] = useState(today)
  const [fromTime, setFromTime] = useState("09:00")
  const [toTime, setToTime] = useState("17:00")
  const [reason, setReason] = useState("")

  let window: { starts_at: string; ends_at: string } | null = null
  try {
    window = allDay
      ? { starts_at: localToUtcIso(fromDate, "00:00", timeZone), ends_at: localToUtcIso(addDays(toDate, 1), "00:00", timeZone) }
      : { starts_at: localToUtcIso(fromDate, fromTime, timeZone), ends_at: localToUtcIso(toDate, toTime, timeZone) }
  } catch {
    window = null
  }
  const invalid = !window || window.ends_at <= window.starts_at
  // The slot finder only applies an extra opening to the resource it names, so one for everyone would do nothing.
  const isResource = resourceId !== "" && resourceId !== "all" && !resourceId.startsWith("loc:")
  const needsResource = kind === "extra_open" && !isResource

  return (
    <form className="grid gap-3 sm:grid-cols-2" aria-label={t("resources.closures.add")}
      onSubmit={(e) => {
        e.preventDefault()
        if (!window || invalid || needsResource) return
        add.mutate({ ...window, kind, ...targetBody(resourceId), reason: reason.trim() || null }, {
          onSuccess: () => { setReason(""); toast.success(t("resources.closures.added")) },
          onError: (err) => toastError(err),
        })
      }}>
      <div className="space-y-1">
        <Label htmlFor="closure-kind">{t("resources.closures.kind")}</Label>
        <select id="closure-kind" className={SELECT} value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
          <option value="closed">{t("resources.closures.kinds.closed")}</option>
          <option value="extra_open">{t("resources.closures.kinds.extra_open")}</option>
        </select>
      </div>
      <div className="space-y-1">
        <Label htmlFor="closure-resource">{t("resources.closures.appliesTo")}</Label>
        <select id="closure-resource" className={SELECT} value={resourceId} onChange={(e) => setResourceId(e.target.value)}>
          {kind === "extra_open" ? <option value="" disabled>{t("resources.closures.chooseResource")}</option>
            : locations.length > 1 ? <>
              <option value="all">{t("resources.closures.allLocations")}</option>
              {locations.map((l) => <option key={l.id} value={l.is_default ? "" : `loc:${l.id}`}>{t("resources.closures.everyoneAt", { location: l.name })}</option>)}
            </> : <option value="">{everyoneLabel}</option>}
          {resources.filter((r) => r.status === "active").map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
      </div>
      {timeZone !== businessZone ? <p className="text-sm text-muted-foreground sm:col-span-2">{t("resources.closures.zoneNote", { zone: timeZone })}</p> : null}
      <label className="flex items-center gap-2 text-sm sm:col-span-2">
        <input type="checkbox" className="size-4" checked={allDay} onChange={(e) => setAllDay(e.target.checked)} />
        {t("resources.closures.allDay")}
      </label>
      <div className="flex gap-2">
        <div className="flex-1 space-y-1">
          <Label htmlFor="closure-from">{t("resources.closures.from")}</Label>
          <Input id="closure-from" type="date" value={fromDate} onChange={(e) => { setFromDate(e.target.value); if (e.target.value > toDate) setToDate(e.target.value) }} />
        </div>
        {allDay ? null : <div className="space-y-1"><Label htmlFor="closure-from-time">{t("resources.closures.fromTime")}</Label><Input id="closure-from-time" type="time" value={fromTime} onChange={(e) => setFromTime(e.target.value)} /></div>}
      </div>
      <div className="flex gap-2">
        <div className="flex-1 space-y-1">
          <Label htmlFor="closure-to">{t("resources.closures.to")}</Label>
          <Input id="closure-to" type="date" value={toDate} min={fromDate} onChange={(e) => setToDate(e.target.value)} />
        </div>
        {allDay ? null : <div className="space-y-1"><Label htmlFor="closure-to-time">{t("resources.closures.toTime")}</Label><Input id="closure-to-time" type="time" value={toTime} onChange={(e) => setToTime(e.target.value)} /></div>}
      </div>
      <div className="space-y-1 sm:col-span-2">
        <Label htmlFor="closure-reason">{t("resources.closures.reason")}</Label>
        <Input id="closure-reason" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} />
      </div>
      {invalid ? <p role="alert" className="text-sm text-destructive sm:col-span-2">{t("resources.closures.endBeforeStart")}</p> : null}
      {needsResource ? <p role="alert" className="text-sm text-destructive sm:col-span-2">{t("resources.closures.extraNeedsResource")}</p> : null}
      <div className="sm:col-span-2"><Button type="submit" disabled={add.isPending || invalid || needsResource}>{t("resources.closures.add")}</Button></div>
    </form>
  )
}

/** Closures (holidays, sick days) and extra openings, for the whole business or one resource. */
export function ClosuresCard({ resources, canEdit }: { resources: Resource[]; canEdit: boolean }) {
  const { t } = useTranslation()
  const { businessTimeZone: tz, permissions } = useSession()
  const exceptions = useExceptions()
  const remove = useDeleteException()
  const locations = useLocations()
  const now = useNow()
  const names = new Map(resources.map((r) => [r.id, r.name]))
  const locs = permissions.has("org.location.read") ? locations.data ?? [] : []
  const defaultLoc = locs.find((l) => l.is_default)
  const zoneFor = (target: Target) => {
    if (target === "all") return tz
    const locationId = target.startsWith("loc:") ? target.slice(4) : target ? resources.find((r) => r.id === target)?.location_id : defaultLoc?.id
    return locs.find((l) => l.id === locationId)?.timezone ?? tz
  }
  type Exception = NonNullable<typeof exceptions.data>[number]
  const targetOf = (x: Exception): Target => x.resource_id ? x.resource_id : x.location_id === null ? "all" : x.location_id === defaultLoc?.id || !x.location_id ? "" : `loc:${x.location_id}`
  const everyoneLabel = locs.length > 1 && defaultLoc ? t("resources.closures.everyoneAt", { location: defaultLoc.name }) : t("resources.closures.everyone")
  const labelOf = (x: Exception) => {
    if (x.resource_id) return names.get(x.resource_id) ?? t("resources.closures.unknownResource")
    if (x.location_id === null) return t("resources.closures.allLocations")
    const loc = locs.find((l) => l.id === x.location_id)
    return loc && !loc.is_default && locs.length > 1 ? t("resources.closures.everyoneAt", { location: loc.name }) : everyoneLabel
  }
  const when = (iso: string, x: Exception) => formatInZone(iso, zoneFor(targetOf(x)), "EEE d MMM yyyy, HH:mm")
  const upcoming = (exceptions.data ?? []).filter((x) => new Date(x.ends_at) > now).sort((a, b) => a.starts_at.localeCompare(b.starts_at))

  return (
    <Card>
      <CardHeader><CardTitle className="text-base">{t("resources.closures.title")}</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">{t("resources.closures.hint", { zone: tz })}</p>
        {exceptions.error ? <QueryError error={exceptions.error} onRetry={() => exceptions.refetch()} />
          : exceptions.isPending ? <Skeleton className="h-16 w-full" />
          : upcoming.length === 0 ? <p className="text-sm text-muted-foreground">{t("resources.closures.none")}</p>
          : (
            <ul className="divide-y rounded-md border" aria-label={t("resources.closures.upcoming")}>
              {upcoming.map((x) => (
                <li key={x.id} className="flex flex-wrap items-center gap-2 p-2 text-sm">
                  <Badge variant={x.kind === "closed" ? "secondary" : "outline"}>{t(`resources.closures.kinds.${x.kind}`)}</Badge>
                  <span>{when(x.starts_at, x)} – {when(x.ends_at, x)}</span>
                  <span className="text-muted-foreground">{labelOf(x)}</span>
                  {x.reason ? <span className="text-muted-foreground">· {x.reason}</span> : null}
                  {canEdit ? <Button size="sm" variant="ghost" className="ml-auto" disabled={remove.isPending}
                    aria-label={t("resources.closures.removeFor", { when: when(x.starts_at, x) })}
                    onClick={() => remove.mutate(x.id, { onSuccess: () => toast.success(t("resources.closures.removed")), onError: (e) => toastError(e) })}>{t("resources.closures.remove")}</Button> : null}
                </li>
              ))}
            </ul>
          )}
        {canEdit ? <AddClosureForm resources={resources} locations={locs} zoneFor={zoneFor} everyoneLabel={everyoneLabel} businessZone={tz} /> : null}
      </CardContent>
    </Card>
  )
}
