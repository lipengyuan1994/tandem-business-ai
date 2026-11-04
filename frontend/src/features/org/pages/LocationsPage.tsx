import { Table } from "@/components/ui/table"
import { QueryError } from "@/components/common/QueryError"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { OpeningHoursItem } from "@/api/types"
import { useSession } from "@/app/session-context"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { toastError } from "@/lib/errors"
import { useCreateLocation, useLocations, useOpeningHours, useReplaceHours } from "../hooks"

const DAYS = [0, 1, 2, 3, 4, 5, 6] as const
const hhmm = (v: string) => v.slice(0, 5)

/** One row per weekday; a day may have several intervals (lunch break) — MVP edits the first two. */
function HoursEditor({ locationId, canEdit }: { locationId: string; canEdit: boolean }) {
  const hours = useOpeningHours(locationId)
  if (hours.error) return <QueryError error={hours.error} onRetry={() => hours.refetch()} />
  if (hours.isPending || !hours.data) return <Skeleton className="h-48 w-full" />
  return <HoursForm key={`${locationId}-${hours.dataUpdatedAt}`} locationId={locationId} canEdit={canEdit} initial={hours.data} />
}

function HoursForm({ locationId, canEdit, initial }: { locationId: string; canEdit: boolean; initial: OpeningHoursItem[] }) {
  const { t } = useTranslation()
  const replace = useReplaceHours(locationId)
  const [draft, setDraft] = useState<OpeningHoursItem[]>(initial)

  const forDay = (d: number) => draft.filter((h) => h.weekday === d)
  const setDay = (d: number, items: OpeningHoursItem[]) => setDraft([...draft.filter((h) => h.weekday !== d), ...items].sort((a, b) => a.weekday - b.weekday || a.opens.localeCompare(b.opens)))

  return (
    <div className="space-y-2">
      <Table>
        <caption className="sr-only">{t("settings.hours.title")}</caption>
        <thead><tr className="text-left text-muted-foreground"><th className="py-1">{t("settings.hours.day")}</th><th>{t("settings.hours.opens")}</th><th>{t("settings.hours.closes")}</th><th><span className="sr-only">{t("common.actions")}</span></th></tr></thead>
        <tbody>
          {DAYS.map((d) => {
            const items = forDay(d)
            return (
              <tr key={d} className="border-t">
                <th scope="row" className="py-2 text-left font-medium">{t(`weekdays.${d}`)}</th>
                {items.length === 0 ? (
                  <td colSpan={2} className="text-muted-foreground">{t("settings.hours.closed")}</td>
                ) : (
                  <>
                    <td><Input aria-label={t("settings.hours.opensFor", { day: t(`weekdays.${d}`) })} type="time" disabled={!canEdit} value={hhmm(items[0]!.opens)}
                      onChange={(e) => setDay(d, [{ ...items[0]!, opens: e.target.value }, ...items.slice(1)])} /></td>
                    <td><Input aria-label={t("settings.hours.closesFor", { day: t(`weekdays.${d}`) })} type="time" disabled={!canEdit} value={hhmm(items[0]!.closes)}
                      onChange={(e) => setDay(d, [{ ...items[0]!, closes: e.target.value }, ...items.slice(1)])} /></td>
                  </>
                )}
                <td className="text-right">
                  {canEdit ? (
                    items.length === 0
                      ? <Button size="sm" variant="ghost" onClick={() => setDay(d, [{ weekday: d, opens: "09:00", closes: "17:00" }])}>{t("settings.hours.open")}</Button>
                      : <Button size="sm" variant="ghost" onClick={() => setDay(d, [])}>{t("settings.hours.close")}</Button>
                  ) : null}
                </td>
              </tr>
            )
          })}
        </tbody>
      </Table>
      {canEdit ? (
        <Button disabled={replace.isPending} onClick={() => replace.mutate(draft.map((h) => ({ ...h, opens: hhmm(h.opens), closes: hhmm(h.closes) })),
          { onSuccess: () => toast.success(t("common.saved")), onError: (e) => toastError(e) })}>
          {t("settings.hours.save")}
        </Button>
      ) : null}
    </div>
  )
}

export function LocationsPage() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const locations = useLocations()
  const create = useCreateLocation()
  const [selected, setSelected] = useState<string | undefined>()
  const [newName, setNewName] = useState("")
  const canEdit = permissions.has("org.location.write")
  const current = selected ?? locations.data?.find((l) => l.is_default)?.id

  if (locations.error) return <QueryError error={locations.error} onRetry={() => locations.refetch()} />
  if (locations.isPending) return <Skeleton className="h-64 w-full" />
  return (
    <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
      <div className="space-y-2">
        <ul className="space-y-1">
          {locations.data?.map((l) => (
            <li key={l.id}>
              <Button variant={l.id === current ? "secondary" : "ghost"} className="w-full justify-between" onClick={() => setSelected(l.id)} aria-pressed={l.id === current}>
                <span>{l.name}</span>
                {l.is_default ? <Badge variant="outline">{t("settings.locations.default")}</Badge> : null}
              </Button>
            </li>
          ))}
        </ul>
        {canEdit ? (
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (newName.trim()) create.mutate({ name: newName.trim(), is_default: false }, { onSuccess: () => setNewName(""), onError: (err) => toastError(err) }) }}>
            <Label htmlFor="new-location" className="sr-only">{t("settings.locations.new")}</Label>
            <Input id="new-location" placeholder={t("settings.locations.new")} value={newName} onChange={(e) => setNewName(e.target.value)} />
            <Button type="submit" variant="outline" disabled={create.isPending}>{t("common.add")}</Button>
          </form>
        ) : null}
      </div>
      {current ? (
        <Card>
          <CardHeader><CardTitle className="text-base">{t("settings.hours.title")}</CardTitle></CardHeader>
          <CardContent><HoursEditor locationId={current} canEdit={canEdit} /></CardContent>
        </Card>
      ) : null}
    </div>
  )
}
