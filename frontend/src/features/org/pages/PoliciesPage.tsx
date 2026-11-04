import { QueryError } from "@/components/common/QueryError"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { DEFAULT_TERMS, type TermKey } from "@/app/terminology"
import type { BusinessSettings } from "@/api/types"
import { useSession } from "@/app/session-context"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { toastError } from "@/lib/errors"
import { useSettings, useUpdateSettings } from "../hooks"

type NumberKey = "hold_minutes" | "booking_lead_time_minutes" | "retention_days_conversations"
type BoolKey = "allow_self_approval" | "allow_backorders" | "public_chat_enabled"

export function PoliciesPage() {
  const settings = useSettings()
  if (settings.error) return <QueryError error={settings.error} onRetry={() => settings.refetch()} />
  if (settings.isPending || !settings.data) return <Skeleton className="h-64 w-full max-w-xl" />
  // Keyed by version: a reload (or someone else's save) starts a fresh draft.
  return <PoliciesForm key={settings.data.version} initial={settings.data.settings} version={settings.data.version} onReload={() => void settings.refetch()} />
}

function PoliciesForm({ initial, version, onReload }: { initial: BusinessSettings; version: number; onReload: () => void }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const update = useUpdateSettings()
  const [draft, setDraft] = useState<BusinessSettings>(initial)
  const [conflict, setConflict] = useState(false)
  const [taxPercent, setTaxPercent] = useState(String(Math.round(Number(initial.sales_tax_rate ?? 0) * 10000) / 100))
  const validTax = /^\d+(\.\d{1,2})?$/.test(taxPercent) && Number(taxPercent) <= 100
  const canEdit = permissions.has("org.settings.write")
  const canAi = permissions.has("ai.settings.write")

  const num = (key: NumberKey) => (
    <div className="space-y-1">
      <Label htmlFor={key}>{t(`settings.policies.${key}`)}</Label>
      <Input id={key} type="number" disabled={!canEdit} value={draft[key] ?? ""} onChange={(e) => setDraft({ ...draft, [key]: Number(e.target.value) })} />
    </div>
  )
  const bool = (key: BoolKey) => (
    <div className="flex items-start gap-2">
      <Checkbox id={key} disabled={!canEdit} checked={!!draft[key]} onCheckedChange={(c) => setDraft({ ...draft, [key]: c === true })} />
      <div>
        <Label htmlFor={key}>{t(`settings.policies.${key}`)}</Label>
        <p className="text-xs text-muted-foreground">{t(`settings.policies.${key}Help`)}</p>
      </div>
    </div>
  )

  return (
    <div className="max-w-xl space-y-6">
      <section className="space-y-3"><h2 className="font-medium">{t("settings.policies.scheduling")}</h2>{num("hold_minutes")}{num("booking_lead_time_minutes")}</section>
      <section className="space-y-3"><h2 className="font-medium">{t("settings.policies.approvals")}</h2>{bool("allow_self_approval")}{bool("allow_backorders")}</section>
      <section className="space-y-3"><h2 className="font-medium">{t("settings.policies.customers")}</h2>{bool("public_chat_enabled")}{num("retention_days_conversations")}</section>
      <section className="space-y-2">
        <h2 className="font-medium">{t("settings.policies.terminology")}</h2>
        <p className="text-xs text-muted-foreground">{t("settings.policies.terminologyHelp")}</p>
        {(Object.keys(DEFAULT_TERMS) as TermKey[]).map((key) => (
          <div key={key} className="space-y-1">
            <Label htmlFor={`term-${key}`}>{DEFAULT_TERMS[key]}</Label>
            <Input id={`term-${key}`} disabled={!canEdit} maxLength={40} placeholder={DEFAULT_TERMS[key]} value={draft.terminology?.[key] ?? ""}
              onChange={(e) => {
                const terminology = { ...draft.terminology }
                if (e.target.value) terminology[key] = e.target.value
                else delete terminology[key]
                setDraft({ ...draft, terminology })
              }} />
          </div>
        ))}
      </section>
      <section className="space-y-2">
        <h2 className="font-medium">{t("settings.policies.sales")}</h2>
        <Label htmlFor="sales-tax-rate">{t("settings.policies.salesTaxRate")}</Label>
        <Input id="sales-tax-rate" type="number" min="0" max="100" step="0.01" disabled={!canEdit} value={taxPercent} aria-invalid={!validTax} aria-describedby="sales-tax-help" onChange={(e) => setTaxPercent(e.target.value)} />
        <p id="sales-tax-help" className="text-xs text-muted-foreground">{t("settings.policies.salesTaxHelp")}</p>
        {!validTax ? <p role="alert" className="text-sm text-destructive">{t("settings.policies.salesTaxInvalid")}</p> : null}
      </section>
      <section className="space-y-2">
        <h2 className="font-medium">{t("settings.policies.ai")}</h2>
        <Label htmlFor="ai_policy">{t("settings.policies.ai_policy")}</Label>
        <select id="ai_policy" disabled={!canEdit || !canAi} className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={draft.ai_policy}
          onChange={(e) => setDraft({ ...draft, ai_policy: e.target.value as BusinessSettings["ai_policy"] })}>
          {(["hosted_vultr", "hosted_any", "strict_private"] as const).map((p) => <option key={p} value={p}>{t(`settings.policies.aiPolicies.${p}`)}</option>)}
        </select>
        <p className="text-xs text-muted-foreground">{t(`settings.policies.aiPolicyHelp.${draft.ai_policy}`)}</p>
      </section>
      {canEdit ? (
        <Button disabled={update.isPending || !validTax} onClick={() => update.mutate({ version, settings: { ...draft, sales_tax_rate: (Number(taxPercent) / 100).toFixed(4) } },
          { onSuccess: () => toast.success(t("common.saved")), onError: (e) => toastError(e, () => setConflict(true)) })}>
          {t("common.save")}
        </Button>
      ) : null}
      <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); onReload() }} />
    </div>
  )
}
