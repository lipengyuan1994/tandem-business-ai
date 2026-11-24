import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { Schemas } from "@/api/types"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toastError } from "@/lib/errors"
import { useUpdateLocation } from "../hooks"

type Location = Schemas["LocationOut"]
const SELECT = "h-9 w-full rounded-md border bg-background px-3 text-sm"

/** Name, address, time zone and default flag of one location. Structured addresses from an import are shown, not edited. */
export function LocationDetails({ location, canEdit, onReload }: { location: Location; canEdit: boolean; onReload: () => void }) {
  const { t } = useTranslation()
  const update = useUpdateLocation(location.id)
  const address = location.address ?? {}
  const simpleAddress = Object.keys(address).every((k) => k === "line")
  const [name, setName] = useState(location.name)
  const [line, setLine] = useState(typeof address.line === "string" ? address.line : "")
  const [timezone, setTimezone] = useState(location.timezone)
  const [conflict, setConflict] = useState(false)
  const zones = Intl.supportedValuesOf("timeZone")
  const changed = name.trim() !== location.name || timezone !== location.timezone || (simpleAddress && line.trim() !== (address.line ?? ""))

  const save = (patch: Omit<Schemas["LocationUpdate"], "version">, done: string) =>
    update.mutate({ version: location.version, ...patch }, { onSuccess: () => toast.success(done), onError: (e) => toastError(e, () => setConflict(true)) })

  return (
    <form className="grid gap-3 sm:grid-cols-2" aria-label={t("settings.locations.details")} onSubmit={(e) => {
      e.preventDefault()
      if (!name.trim() || !changed) return
      save({ name: name.trim(), timezone, ...(simpleAddress ? { address: line.trim() ? { line: line.trim() } : {} } : {}) }, t("common.saved"))
    }}>
      <div className="space-y-1">
        <Label htmlFor="loc-name">{t("settings.locations.name")}</Label>
        <Input id="loc-name" required maxLength={120} disabled={!canEdit} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="loc-tz">{t("settings.locations.timezone")}</Label>
        <select id="loc-tz" className={SELECT} disabled={!canEdit} value={timezone} onChange={(e) => setTimezone(e.target.value)}>
          {(zones.includes(location.timezone) ? zones : [location.timezone, ...zones]).map((z) => <option key={z} value={z}>{z}</option>)}
        </select>
      </div>
      <div className="space-y-1 sm:col-span-2">
        <Label htmlFor="loc-address">{t("settings.locations.address")}</Label>
        {simpleAddress
          ? <Input id="loc-address" maxLength={300} disabled={!canEdit} value={line} onChange={(e) => setLine(e.target.value)} />
          : <p id="loc-address" className="text-sm">{Object.values(address).join(", ")}</p>}
        <p className="text-xs text-muted-foreground">{t("settings.locations.addressHint")}</p>
      </div>
      {canEdit ? (
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <Button type="submit" disabled={update.isPending || !changed || !name.trim()}>{t("common.save")}</Button>
          {location.is_default ? null : <Button type="button" variant="outline" disabled={update.isPending} onClick={() => save({ is_default: true }, t("settings.locations.madeDefault", { name: location.name }))}>{t("settings.locations.makeDefault")}</Button>}
        </div>
      ) : null}
      <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); onReload() }} />
    </form>
  )
}
