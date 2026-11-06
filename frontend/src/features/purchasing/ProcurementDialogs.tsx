import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { Schemas } from "@/api/types"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { describeError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"
import { QuoteFields } from "@/widget/QuoteFields"
import { blankQuote, quoteInput, validQuote } from "@/widget/quote-model"
import { fieldInvalid } from "./validation"
import { FieldError } from "./FieldError"
import { useProcurementAction, usePurchasingPickers } from "./hooks"
import { ProposalFeedback } from "./ProposalFeedback"

export function EnterQuoteDialog({ request, supplier, onClose }: { request: Schemas["QuoteRequestOut"]; supplier: string; onClose: () => void }) {
  const { t } = useTranslation()
  const pickers = usePurchasingPickers()
  const save = useProcurementAction()
  const [draft, setDraft] = useState(blankQuote)
  const currency = pickers.data?.currency
  const ready = !!currency && validQuote(draft, request.lines, currency) && !pickers.error
  return <Dialog open onOpenChange={(open) => { if (!open && !save.isPending) onClose() }}><DialogContent className="max-h-[90dvh] overflow-y-auto">
    <DialogHeader><DialogTitle>{t("procurement.enter")}</DialogTitle><DialogDescription>{t("procurement.approvalHint")}</DialogDescription></DialogHeader>
    {save.data ? <ProposalFeedback proposal={save.data} /> : <form id="enter-quote" className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (ready && currency && !save.isPending) save.mutate({ kind: "record", body: { ...quoteInput(draft, request.lines, currency), quote_request_id: request.id, supplier_id: supplier, currency, source: "staff" } }) }}>
      {pickers.error ? <QueryError error={pickers.error} onRetry={() => pickers.refetch()} /> : currency ? <QuoteFields draft={draft} onChange={setDraft} lines={request.lines} currency={currency} errors={save.error instanceof ApiProblem ? save.error.fieldErrors() : undefined} /> : <p>{t("common.loading")}</p>}
      {save.error ? <p role="alert">{describeError(save.error)}</p> : null}
    </form>}
    <DialogFooter><Button variant="outline" disabled={save.isPending} onClick={onClose}>{t("common.close")}</Button>{!save.data ? <Button form="enter-quote" disabled={!ready || save.isPending}>{t("common.save")}</Button> : null}</DialogFooter>
  </DialogContent></Dialog>
}
export function FollowupDialog({ supplier, about, onClose }: { supplier: string; about: Schemas["SupplierFollowup"]["about"]; onClose: () => void }) {
  const { t } = useTranslation()
  const save = useProcurementAction()
  const [body, setBody] = useState("")
  return <Dialog open onOpenChange={(open) => { if (!open && !save.isPending) onClose() }}><DialogContent>
    <DialogHeader><DialogTitle>{t("procurement.followup")}</DialogTitle><DialogDescription>{t("procurement.approvalHint")}</DialogDescription></DialogHeader>
    {save.data ? <ProposalFeedback proposal={save.data} /> : <form id="supplier-followup" className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (body.trim() && !save.isPending) save.mutate({ kind: "followup", body: { supplier_id: supplier, about, body: body.trim() } }) }}>
      <Label htmlFor="followup-body">{t("procurement.message")}</Label><Textarea dictation={{ context: "purchasing" }} id="followup-body" aria-invalid={fieldInvalid(save.error, "body")} required maxLength={2000} value={body} onChange={(e) => setBody(e.target.value)} /><FieldError error={save.error} field="body" />
      {save.error ? <p role="alert">{describeError(save.error)}</p> : null}
    </form>}
    <DialogFooter><Button variant="outline" disabled={save.isPending} onClick={onClose}>{t("common.close")}</Button>{!save.data ? <Button form="supplier-followup" disabled={!body.trim() || save.isPending}>{t("procurement.followup")}</Button> : null}</DialogFooter>
  </DialogContent></Dialog>
}
