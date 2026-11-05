import { toast } from "sonner"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { BusinessSettings } from "@/api/types"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { useSettings, useUpdateSettings } from "@/features/org/hooks"
import { toastError } from "@/lib/errors"

export function SendDelaySetting() {
  const settings = useSettings()
  if (settings.error) return <QueryError error={settings.error} onRetry={() => settings.refetch()} />
  if (!settings.data) return <Skeleton className="h-24 w-full" />
  return <SendDelayForm key={settings.data.version} initial={settings.data.settings} version={settings.data.version} onReload={() => void settings.refetch()} />
}
function SendDelayForm({ initial, version, onReload }: { initial: BusinessSettings; version: number; onReload: () => void }) {
  const { t } = useTranslation()
  const save = useUpdateSettings()
  const [minutes, setMinutes] = useState(String((initial.outbound_send_delay_seconds ?? 120) / 60))
  const [conflict, setConflict] = useState(false)
  // The API stores seconds. Preserve non-whole-minute values from existing settings.
  const seconds = Number(minutes) * 60
  const valid = minutes.trim() !== "" && Number.isInteger(seconds) && seconds >= 0 && seconds <= 900
  return <Card><CardHeader><CardTitle>{t("ai.delivery.setting")}</CardTitle></CardHeader><CardContent>
    <form className="space-y-3" onSubmit={(event) => {
      event.preventDefault()
      if (!valid || conflict) return
      save.mutate({ version, settings: { ...initial, outbound_send_delay_seconds: seconds } }, { onSuccess: () => toast.success(t("common.saved")), onError: (error) => toastError(error, () => setConflict(true)) })
    }}>
      <Label htmlFor="send-delay">{t("ai.delivery.minutes")}</Label>
      <Input id="send-delay" type="number" min="0" max="15" step="any" value={minutes} disabled={save.isPending || conflict} aria-invalid={!valid} onChange={(event) => setMinutes(event.target.value)} />
      <p className="text-sm text-muted-foreground">{t("ai.delivery.settingHelp")}</p>
      {!valid ? <p role="alert">{t("ai.delivery.invalidDelay")}</p> : null}
      <Button type="submit" disabled={!valid || conflict || save.isPending}>{t("ai.delivery.saveDelay")}</Button>
      {save.isSuccess ? <p role="status">{t("common.saved")}</p> : null}
    </form>
    <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); onReload() }} />
  </CardContent></Card>
}
