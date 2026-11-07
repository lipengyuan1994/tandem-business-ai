import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { BusinessSettings } from "@/api/types"
import { useSession } from "@/app/session-context"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { useSettings, useUpdateSettings } from "@/features/org/hooks"
import { ApiProblem } from "@/lib/problem"

export function CustomerCareSettings() {
  const settings = useSettings()
  if (settings.error) return <QueryError error={settings.error} onRetry={() => settings.refetch()} />
  if (!settings.data) return <Skeleton className="h-48 w-full" />
  return <CareForm key={settings.data.version} initial={settings.data.settings} version={settings.data.version} onReload={() => void settings.refetch()} />
}
function CareForm({ initial, version, onReload }: { initial: BusinessSettings; version: number; onReload: () => void }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const save = useUpdateSettings()
  const [delay, setDelay] = useState(String(initial.checkin_delay_days ?? 2))
  const [start, setStart] = useState(String(initial.customer_care_quiet_start_hour ?? 20))
  const [end, setEnd] = useState(String(initial.customer_care_quiet_end_hour ?? 8))
  const [tone, setTone] = useState(initial.customer_care_tone ?? "warm")
  const [conflict, setConflict] = useState(false)
  const whole = (value: string, max: number) => /^\d+$/.test(value) && Number(value) <= max
  const valid = whole(delay, 365) && whole(start, 23) && whole(end, 23)
  const number = (id: string, value: string, set: (value: string) => void, max: number) => <div className="space-y-1"><Label htmlFor={id}>{t(`ai.care.${id}`)}</Label><Input id={id} type="number" min={0} max={max} step={1} value={value} aria-invalid={!whole(value, max)} disabled={save.isPending || conflict} onChange={(e) => set(e.target.value)} /></div>
  return <Card><CardHeader><CardTitle>{t("ai.care.settings")}</CardTitle></CardHeader><CardContent>
    <form className="space-y-3" onSubmit={(e) => {
      e.preventDefault()
      if (!valid || conflict) return
      save.mutate({ version, settings: { ...initial, checkin_delay_days: Number(delay), customer_care_quiet_start_hour: Number(start), customer_care_quiet_end_hour: Number(end), customer_care_tone: tone } }, { onError: (error) => { if (error instanceof ApiProblem && error.isConflict) setConflict(true) } })
    }}>
      {number("delay", delay, setDelay, 365)}
      {whole(delay, 365) && Number(delay) === 0 ? <p>{t("ai.care.checkinsOff")}</p> : null}
      <p className="text-sm text-muted-foreground">{t("ai.care.delayHelp")}</p>
      {number("quietStart", start, setStart, 23)}{number("quietEnd", end, setEnd, 23)}
      <p className="text-sm">{t("ai.care.timezone", { timezone: businessTimeZone })}</p>
      {whole(start, 23) && whole(end, 23) && Number(start) === Number(end) ? <p>{t("ai.care.quietOff")}</p> : null}
      <Label htmlFor="care-tone">{t("ai.care.tone")}</Label>
      <select id="care-tone" className="h-9 rounded-md border bg-background px-3 text-sm" value={tone} disabled={save.isPending || conflict} onChange={(e) => setTone(e.target.value as BusinessSettings["customer_care_tone"])}>{(["warm", "formal", "concise"] as const).map((value) => <option key={value} value={value}>{t(`ai.care.tones.${value}`)}</option>)}</select>
      <p><a className="record-link" href="/settings/business">{t("ai.care.profileLinks")}</a></p>
      {!valid ? <p role="alert">{t("ai.care.invalid")}</p> : null}
      {save.error && !(save.error instanceof ApiProblem && save.error.isConflict) ? <QueryError error={save.error} onRetry={() => save.reset()} /> : null}
      {save.isSuccess ? <p role="status">{t("common.saved")}</p> : null}
      <Button type="submit" disabled={!valid || conflict || save.isPending}>{t("ai.care.save")}</Button>
    </form>
    <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); save.reset(); onReload() }} />
  </CardContent></Card>
}
