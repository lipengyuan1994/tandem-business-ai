import { Sparkles } from "lucide-react"
import { useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { problemCode } from "@/features/ai/api"
import { aiErrorText, useAiAvailability } from "@/features/ai/text"

/** Mounted only for editable campaigns and people with both drafting permissions. */
export function CampaignDraftControl({ purpose, inReview, changed, busy, pending, error, saved, onDraft }: {
  purpose: string | null
  inReview: boolean
  changed: boolean
  busy: boolean
  pending: boolean
  error: unknown
  saved: boolean
  onDraft: (purpose: string) => void
}) {
  const { t } = useTranslation()
  const { enabled } = useAiAvailability()
  const [value, setValue] = useState(purpose ?? "")
  const [confirm, setConfirm] = useState(false)
  const control = useRef<HTMLDivElement>(null)
  if (!enabled) return null
  const errorCode = error ? problemCode(error) : null
  const button = (
    <Button type="button" variant="outline" className="w-full sm:w-44" disabled={changed || busy}
      aria-describedby={changed ? "cp-ai-unsaved" : undefined} onClick={() => { if (!inReview) onDraft(value) }}>
      <Sparkles aria-hidden="true" /> {pending ? t("marketing.ai.working") : t("marketing.ai.button")}
    </Button>
  )

  return (
    <div ref={control} tabIndex={-1} aria-busy={pending} className="space-y-2">
      <Label htmlFor="cp-ai-purpose">{t("marketing.ai.purpose")}</Label>
      <Input id="cp-ai-purpose" value={value} maxLength={1000} disabled={busy} onChange={(e) => setValue(e.target.value)} />
      <Dialog open={confirm} onOpenChange={setConfirm}>
        {inReview ? <DialogTrigger asChild>{button}</DialogTrigger> : button}
        <DialogContent onCloseAutoFocus={(e) => { if (busy) { e.preventDefault(); control.current?.focus() } }}>
          <DialogHeader>
            <DialogTitle>{t("marketing.editConfirm.title")}</DialogTitle>
            <DialogDescription>{t("marketing.editConfirm.text")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirm(false)}>{t("common.cancel")}</Button>
            <Button type="button" disabled={changed || busy} onClick={() => { setConfirm(false); onDraft(value) }}>{t("marketing.editConfirm.confirm")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <div className="min-h-20 text-sm">
        {changed ? <p id="cp-ai-unsaved" className="text-muted-foreground">{t("marketing.ai.unsaved")}</p>
          : pending ? <p role="status" className="text-muted-foreground">{t("marketing.ai.working")}</p>
          : error ? <p role="alert" className="text-destructive">{errorCode === "ai.invalid_model_output" ? t("marketing.ai.invalidDraft") : aiErrorText(t, errorCode)}</p>
          : saved ? <p role="status" className="text-muted-foreground">{t("marketing.ai.saved")}</p> : null}
      </div>
    </div>
  )
}
