import { QueryError } from "@/components/common/QueryError"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { useCustomerMatch } from "@/features/crm/hooks"
import { useBusiness } from "@/features/org/hooks"
import { describeError, toastError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"
import { useCreateDocument } from "../hooks"
import { DOCUMENT_ROUTES } from "../model"
import { LinesEditor } from "./LinesEditor"
import { blankLine, hasFieldError, lineInputs, validLines } from "../line-draft"

export function CreateDocumentDialog({ family, onOpenChange }: { family: "quotes" | "invoices"; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const create = useCreateDocument(family)
  const business = useBusiness()
  const currency = business.data?.currency
  const [query, setQuery] = useState("")
  const [customerId, setCustomerId] = useState("")
  const [address, setAddress] = useState("")
  const [dueDate, setDueDate] = useState("")
  const [notes, setNotes] = useState("")
  const [lines, setLines] = useState(() => [blankLine()])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const matches = useCustomerMatch(customerId ? "" : query)
  const ready = customerId && currency && !business.error && validLines(lines, currency)
  return <Dialog open onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl">
      <DialogHeader><DialogTitle>{t(`sales.new.${family}`)}</DialogTitle><DialogDescription>{t("sales.newHint")}</DialogDescription></DialogHeader>
      <form id="sales-create" className="space-y-4" onSubmit={(e) => {
        e.preventDefault()
        if (!ready || !currency) return
        create.mutate({ customer_id: customerId, bill_to_address: address || null, due_date: dueDate || null, notes: notes || null, lines: lineInputs(lines, currency) }, {
          onSuccess: (d) => { toast.success(t("sales.created")); onOpenChange(false); void navigate({ to: DOCUMENT_ROUTES[family], params: { id: d.id } }) },
          onError: (err) => { if (err instanceof ApiProblem && err.status === 422) setErrors(err.fieldErrors()); toastError(err) },
        })
      }}>
        <fieldset disabled={create.isPending} className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="sales-customer">{t("sales.fields.customer")}</Label>
            <Input id="sales-customer" value={query} autoComplete="off" placeholder={t("schedule.customerHint")}
              aria-invalid={hasFieldError(errors, "customer_id")} onChange={(e) => { setQuery(e.target.value); setCustomerId(""); setErrors({}) }} />
            {hasFieldError(errors, "customer_id") ? <p role="alert">{t("sales.invalidField")}</p> : null}
            {!customerId && query.trim().length >= 2 ? <>
              {matches.isPending ? <p role="status">{t("common.loading")}</p> : null}
              {matches.error ? <QueryError error={matches.error} onRetry={() => matches.refetch()} /> : null}
              {matches.data?.length === 0 ? <p>{t("sales.noCustomers")}</p> : null}
              {matches.data?.length ? <ul className="rounded-md border" aria-label={t("schedule.customerMatches")}>
                {matches.data.slice(0, 5).map((m) => <li key={m.customer_id}><button type="button" className="w-full px-3 py-2 text-left text-sm hover:bg-accent" onClick={() => { setCustomerId(m.customer_id); setQuery(m.display_name) }}>{m.display_name}</button></li>)}
              </ul> : null}
            </> : null}
          </div>
          <div className="space-y-1"><Label htmlFor="sales-address">{t("sales.fields.address")}</Label><Textarea id="sales-address" maxLength={2000} value={address} aria-invalid={hasFieldError(errors, "bill_to_address")} onChange={(e) => setAddress(e.target.value)} /></div>
          <div className="space-y-1"><Label htmlFor="sales-due">{t("sales.fields.dueDate")}</Label><Input id="sales-due" type="date" value={dueDate} aria-invalid={hasFieldError(errors, "due_date")} onChange={(e) => setDueDate(e.target.value)} /></div>
          {business.isPending ? <p role="status">{t("common.loading")}</p> : null}
          {business.error ? <div role="alert"><p>{describeError(business.error)}</p><Button type="button" variant="outline" onClick={() => void business.refetch()}>{t("common.retry")}</Button></div> : null}
          {currency && !business.error ? <LinesEditor lines={lines} currency={currency} onChange={(next) => { setLines(next); setErrors({}) }} errors={errors} /> : null}
          <div className="space-y-1"><Label htmlFor="sales-notes">{t("sales.fields.notes")}</Label><Textarea dictation={{ context: "note" }} id="sales-notes" maxLength={4000} value={notes} aria-invalid={hasFieldError(errors, "notes")} onChange={(e) => setNotes(e.target.value)} /></div>
        </fieldset>
      </form>
      <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button><Button type="submit" form="sales-create" disabled={!ready || create.isPending}>{t("sales.createDraft")}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}
