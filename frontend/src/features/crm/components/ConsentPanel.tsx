import { QueryError } from "@/components/common/QueryError"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { Consent, ConsentIn } from "@/api/types"
import { useSession } from "@/app/session-context"
import { LocalTime } from "@/components/common/LocalTime"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toastError } from "@/lib/errors"
import { useConsents, useRecordConsent } from "../hooks"

const KEYS: [ConsentIn["channel"], ConsentIn["purpose"]][] = [["email", "marketing"], ["sms", "marketing"], ["email", "service_updates"], ["sms", "service_updates"]]

/** Consent is shown as its full history per channel and purpose, never as a bare checkbox (M03 UI). */
export function ConsentPanel({ customerId }: { customerId: string }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const consents = useConsents(customerId)
  const record = useRecordConsent(customerId)
  const [form, setForm] = useState<ConsentIn>({ channel: "email", purpose: "marketing", status: "granted", source: "staff", evidence: "" })
  const history = consents.data ?? []
  const latest = (channel: string, purpose: string): Consent | undefined => history.find((c) => c.channel === channel && c.purpose === purpose)

  if (consents.error) return <QueryError error={consents.error} onRetry={() => consents.refetch()} />
  if (consents.isPending) return <p role="status">{t("common.loading")}</p>
  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">{t("crm.consent.proactiveHelp")}</p>
      <dl className="grid gap-2 sm:grid-cols-2">
        {KEYS.map(([channel, purpose]) => {
          const c = latest(channel, purpose)
          return (
            <div key={`${channel}-${purpose}`} className="rounded-md border p-3">
              <dt className="text-sm text-muted-foreground">{t(`crm.consent.channels.${channel}`)} · {t(`crm.consent.purposes.${purpose}`)}</dt>
              <dd className="font-medium">{c ? t(`crm.consent.statuses.${c.status}`) : t("crm.consent.none")}</dd>
              {c && purpose === "service_updates" ? <dd className="text-sm">{t(c.source === "form" ? "crm.consent.requestOnly" : c.status === "granted" && ["staff", "import"].includes(c.source) && channel === "email" ? "crm.consent.proactiveAllowed" : "crm.consent.proactiveNotAllowed")}</dd> : null}
            </div>
          )
        })}
      </dl>
      <section aria-labelledby="consent-history-h">
        <h3 id="consent-history-h" className="mb-2 font-medium">{t("crm.consent.history")}</h3>
        {history.length === 0 ? <p className="text-sm text-muted-foreground">{t("crm.consent.noHistory")}</p> : (
          <ol className="space-y-2 text-sm">
            {history.map((c) => (
              <li key={c.id} className="border-l-2 pl-3">
                <LocalTime iso={c.recorded_at} timeZone={businessTimeZone} pattern="d MMM yyyy HH:mm" /> ·{" "}
                {t(`crm.consent.channels.${c.channel}`)} {t(`crm.consent.purposes.${c.purpose}`)}: <strong>{t(`crm.consent.statuses.${c.status}`)}</strong>{" "}
                <span className="text-muted-foreground">({t(`crm.consent.sources.${c.source}`, { defaultValue: c.source })}){c.source === "form" && c.purpose === "service_updates" ? ` · ${t("crm.consent.requestOnly")}` : ""} “{c.evidence}”</span>
              </li>
            ))}
          </ol>
        )}
      </section>
      <PermissionGate permission="crm.consent.write">
        <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => {
          e.preventDefault()
          record.mutate(form, { onSuccess: () => setForm({ ...form, evidence: "" }), onError: (err) => toastError(err) })
        }}>
          <div className="space-y-1">
            <Label htmlFor="consent-key">{t("crm.consent.what")}</Label>
            <select id="consent-key" className="h-9 rounded-md border bg-background px-3 text-sm" value={`${form.channel}:${form.purpose}`}
              onChange={(e) => { const [channel, purpose] = e.target.value.split(":") as [ConsentIn["channel"], ConsentIn["purpose"]]; setForm({ ...form, channel, purpose }) }}>
              {KEYS.map(([c, p]) => <option key={`${c}:${p}`} value={`${c}:${p}`}>{t(`crm.consent.channels.${c}`)} · {t(`crm.consent.purposes.${p}`)}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="consent-status">{t("crm.consent.status")}</Label>
            <select id="consent-status" className="h-9 rounded-md border bg-background px-3 text-sm" value={form.status}
              onChange={(e) => setForm({ ...form, status: e.target.value as ConsentIn["status"] })}>
              {(["granted", "denied", "withdrawn"] as const).map((s) => <option key={s} value={s}>{t(`crm.consent.statuses.${s}`)}</option>)}
            </select>
          </div>
          <div className="min-w-64 flex-1 space-y-1">
            <Label htmlFor="consent-evidence">{t("crm.consent.evidence")}</Label>
            <Input id="consent-evidence" required value={form.evidence} placeholder={t("crm.consent.evidenceHint")} onChange={(e) => setForm({ ...form, evidence: e.target.value })} />
          </div>
          <Button type="submit" disabled={record.isPending}>{t("crm.consent.record")}</Button>
        </form>
      </PermissionGate>
    </div>
  )
}
