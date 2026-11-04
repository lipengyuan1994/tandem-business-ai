// "Paste supplier reply" on a quote request: the AI reads prices and dates out of pasted e-mail text.
// The server checks every number against the text. Whatever it could not find is marked here, and nothing is
// proposed for approval unless everything checked out.
import { Link } from "@tanstack/react-router"
import { Sparkles } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ApiProblem } from "@/lib/problem"
import { problemCode, useExtractSupplierQuote } from "./api"
import { AiDisabledBanner } from "./AiDisabledBanner"
import { unverifiedLabel, unverifiedLine, unverifiedTop } from "./extract"
import { aiErrorText, useAiAvailability } from "./text"

export const MAX_REPLY_LENGTH = 8000

/** The parts of a quote request this dialog needs (a subset of the purchasing API's quote request). */
export interface QuoteRequestForAi {
  id: string
  lines: { item_id: string; name: string }[]
  suppliers: { supplier_id: string; supplier_name: string }[]
}

export function PasteSupplierReplyButton({ request }: { request: QuoteRequestForAi }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const [open, setOpen] = useState(false)
  if (!permissions.has("ai.assistant.use") || !permissions.has("purchasing.po.write")) return null
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}><Sparkles aria-hidden="true" /> {t("ai.paste.button")}</Button>
      {open ? <PasteSupplierReplyDialog request={request} onClose={() => setOpen(false)} /> : null}
    </>
  )
}

export function PasteSupplierReplyDialog({ request, onClose }: { request: QuoteRequestForAi; onClose: () => void }) {
  const { t } = useTranslation()
  const { enabled } = useAiAvailability()
  const extract = useExtractSupplierQuote(request.id)
  const [supplierId, setSupplierId] = useState(request.suppliers.length === 1 ? request.suppliers[0]!.supplier_id : "")
  const [text, setText] = useState("")
  const out = extract.data
  const valid = !!supplierId && text.trim().length > 0 && text.length <= MAX_REPLY_LENGTH

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !extract.isPending) onClose() }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("ai.paste.title")}</DialogTitle>
          <DialogDescription>{t("ai.paste.description")}</DialogDescription>
        </DialogHeader>
        <AiDisabledBanner />
        <form id="paste-reply" className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (valid && enabled && !extract.isPending) extract.mutate({ supplier_id: supplierId, text }) }}>
          <div className="space-y-1">
            <Label htmlFor="paste-supplier">{t("ai.paste.supplier")}</Label>
            <select id="paste-supplier" className="h-11 w-full rounded-md border bg-background px-2" value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
              <option value="">{t("ai.paste.chooseSupplier")}</option>
              {request.suppliers.map((s) => <option key={s.supplier_id} value={s.supplier_id}>{s.supplier_name}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="paste-text">{t("ai.paste.text")}</Label>
            <Textarea id="paste-text" rows={8} maxLength={MAX_REPLY_LENGTH} value={text} onChange={(e) => setText(e.target.value)} aria-describedby="paste-count" />
            <p id="paste-count" className="text-xs text-muted-foreground">{t("ai.paste.count", { n: text.length, max: MAX_REPLY_LENGTH })}</p>
          </div>
        </form>
        {extract.isPending ? <p role="status">{t("ai.paste.reading")}</p> : null}
        {extract.error ? <p role="alert" className="text-sm">{aiErrorText(t, extract.error instanceof ApiProblem ? problemCode(extract.error) : null)}</p> : null}
        {out ? (
          <section aria-labelledby="paste-result" className="space-y-3 text-sm" aria-live="polite">
            <h3 id="paste-result" className="font-medium">{t("ai.paste.result")}</h3>
            <p role={out.verified ? "status" : "alert"} className="rounded-md border p-3">{out.verified ? t("ai.paste.verified") : t("ai.paste.unverified")}</p>
            <ul className="space-y-1" aria-label={t("ai.paste.line")}>
              {out.extract.lines.map((line, i) => {
                const name = request.lines.find((l) => l.item_id === line.item_id)?.name ?? line.item_id
                const priceBad = unverifiedLine(out.unverified_fields, i, line.item_id, "unit_price")
                const qtyBad = unverifiedLine(out.unverified_fields, i, line.item_id, "quantity_available")
                return (
                  <li key={line.item_id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-md border p-2">
                    <span className="font-medium">{name}</span>
                    <Value label={t("ai.paste.line")} value={line.unit_price} bad={priceBad} />
                    <Value label={t("ai.paste.available")} value={line.quantity_available ?? null} bad={qtyBad} />
                  </li>
                )
              })}
            </ul>
            <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
              <Field label={t("ai.paste.currency")} value={out.extract.currency} bad={unverifiedTop(out.unverified_fields, "currency")} />
              <Field label={t("ai.paste.leadTime")} value={out.extract.lead_time_days == null ? null : t("ai.paste.leadTimeValue", { n: out.extract.lead_time_days })} bad={unverifiedTop(out.unverified_fields, "lead_time_days")} />
              <Field label={t("ai.paste.minimum")} value={out.extract.min_quantity ?? null} bad={unverifiedTop(out.unverified_fields, "min_quantity")} />
              <Field label={t("ai.paste.validUntil")} value={out.extract.valid_until ?? null} bad={unverifiedTop(out.unverified_fields, "valid_until")} />
              {out.extract.notes ? <Field label={t("ai.paste.notes")} value={out.extract.notes} bad={false} /> : null}
            </dl>
            {out.unverified_fields.length ? (
              <div>
                <p className="font-medium">{t("ai.paste.unverifiedList")}</p>
                <ul className="list-disc pl-5">{out.unverified_fields.map((f) => <li key={f}>{unverifiedLabel(t, f, out.extract.lines, request.lines)}</li>)}</ul>
              </div>
            ) : null}
            {out.proposal_id ? (
              <p className="rounded-md border p-3">
                {t("ai.paste.proposal")}{" "}
                <Link className="record-link" to="/approvals/$proposalId" params={{ proposalId: out.proposal_id }}>{t("ai.paste.review")}</Link>
              </p>
            ) : null}
          </section>
        ) : null}
        <DialogFooter>
          <Button variant="outline" disabled={extract.isPending} onClick={onClose}>{t("common.close")}</Button>
          <Button form="paste-reply" type="submit" disabled={!valid || !enabled || extract.isPending}>{extract.isPending ? t("ai.paste.reading") : t("ai.paste.read")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Value({ label, value, bad }: { label: string; value: string | null; bad: boolean }) {
  const { t } = useTranslation()
  return (
    <span>
      <span className="text-muted-foreground">{label}: </span>
      {value ?? <span className="text-muted-foreground">{t("ai.paste.none")}</span>}
      {bad ? <span className="ml-2 rounded-full border border-destructive/50 px-2 text-xs text-destructive">{t("ai.paste.notFound")}</span> : null}
    </span>
  )
}

function Field({ label, value, bad }: { label: string; value: string | null; bad: boolean }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-wrap gap-x-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{value ?? t("ai.paste.none")}{bad ? <span className="ml-2 rounded-full border border-destructive/50 px-2 text-xs text-destructive">{t("ai.paste.notFound")}</span> : null}</dd>
    </div>
  )
}
