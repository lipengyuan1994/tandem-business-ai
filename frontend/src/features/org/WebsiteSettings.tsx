import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { BusinessSettings } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useBusiness } from "./hooks"

import { normalizeEmbedOrigins } from "./embed-origins"

export function WebsiteSettings({ draft, onChange, disabled, onValidity }: { draft: BusinessSettings; onChange: (value: BusinessSettings) => void; disabled: boolean; onValidity: (valid: boolean) => void }) {
  const { t } = useTranslation()
  const business = useBusiness()
  const [origins, setOrigins] = useState((draft.embed_allowed_origins ?? []).join("\n"))
  const [invalid, setInvalid] = useState(false)
  const [copy, setCopy] = useState("")
  const snippet = business.data ? `<script src="${location.origin}/embed.js" data-business="${business.data.slug}" async></script>` : ""
  return <section className="space-y-3 rounded border p-4">
    <h2 className="font-medium">{t("website.title")}</h2>
    <Label htmlFor="embed-origins">{t("website.origins")}</Label>
    <textarea id="embed-origins" className="min-h-28 w-full rounded border p-2 text-sm" disabled={disabled} value={origins} aria-invalid={invalid} aria-describedby="embed-origin-help" onChange={(e) => {
      setOrigins(e.target.value)
      try { const values = normalizeEmbedOrigins(e.target.value, import.meta.env.DEV); onChange({ ...draft, embed_allowed_origins: values }); setInvalid(false); onValidity(true) }
      catch { setInvalid(true); onValidity(false) }
    }} />
    <p id="embed-origin-help" className="text-xs">{t("website.originsHelp")}</p>
    {invalid ? <p role="alert">{t("website.originsInvalid")}</p> : null}
    <Label htmlFor="embed-label">{t("website.label")}</Label>
    <Input id="embed-label" disabled={disabled} maxLength={80} value={draft.embed_button_label ?? "Chat with us"} onChange={(e) => onChange({ ...draft, embed_button_label: e.target.value })} />
    <Label htmlFor="embed-snippet">{t("website.snippet")}</Label>
    <textarea id="embed-snippet" readOnly className="w-full rounded border p-2 text-sm" value={snippet} />
    <Button type="button" disabled={!snippet} onClick={async () => { try { await navigator.clipboard.writeText(snippet); setCopy(t("website.copied")) } catch { setCopy(t("website.copyFailed")) } }}>{t("website.copy")}</Button>
    {copy ? <p role="status">{copy}</p> : null}
    <p className="text-xs">{t("website.saveHelp")}</p>
    <label className="flex min-h-11 items-center gap-2"><input type="checkbox" disabled={disabled} checked={draft.self_service_enabled ?? false} onChange={(e) => onChange({ ...draft, self_service_enabled: e.target.checked })} />{t("website.enabled")}</label>
    <Label htmlFor="notice-hours">{t("website.notice")}</Label>
    <Input id="notice-hours" type="number" min={0} max={8760} required disabled={disabled} value={draft.cancellation_notice_hours ?? 24} onChange={(e) => { const value = Number(e.target.value); onChange({ ...draft, cancellation_notice_hours: value }); onValidity(!invalid && e.target.value !== "" && Number.isInteger(value) && value >= 0 && value <= 8760) }} />
    <Label htmlFor="fee-text">{t("website.fee")}</Label>
    <textarea id="fee-text" className="min-h-20 w-full rounded border p-2 text-sm" disabled={disabled} maxLength={1000} value={draft.cancellation_fee_text ?? ""} onChange={(e) => onChange({ ...draft, cancellation_fee_text: e.target.value || null })} />
  </section>
}
