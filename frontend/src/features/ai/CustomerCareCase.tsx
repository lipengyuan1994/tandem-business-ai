import { useState } from "react"
import { useTranslation } from "react-i18next"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { StatusBadge } from "@/components/common/StatusBadge"
import { useCareCase, useResolveCareCase } from "./customer-care-api"
import { ApiProblem } from "@/lib/problem"
import { CareProposalSummary, CareCustomer } from "./CustomerCareProposal"

export function CustomerCareCase({ id }: { id: string }) {
  const { t } = useTranslation()
  const detail = useCareCase(id)
  const resolve = useResolveCareCase(id)
  const [confirm, setConfirm] = useState(false)
  const [conflict, setConflict] = useState(false)
  if (detail.error) return <QueryError error={detail.error} onRetry={() => detail.refetch()} />
  const c = detail.data
  if (!c) return <p role="status">{t("common.loading")}</p>
  return <section className="space-y-3 rounded-md border p-3" aria-label={t("ai.care.caseDetail")}>
    <p>{t(`ai.care.categories.${c.category}`, { defaultValue: t("ai.care.categories.other") })} · {t(`ai.care.priorities.${c.priority}`, { defaultValue: t("ai.care.priorities.normal") })}</p>
    <StatusBadge status={c.status} />
    <p>{c.customer_id ? <CareCustomer id={c.customer_id} /> : <a className="record-link" href={`/inbox/${encodeURIComponent(c.conversation_id)}`}>{t("ai.care.unknownCustomer")}</a>}</p>
    {c.customer_id ? <p><a className="record-link" href={`/inbox/${encodeURIComponent(c.conversation_id)}`}>{t("ai.care.thread")}</a></p> : null}
    <div className="flex flex-wrap gap-3">
      {c.appointment_id ? <a className="record-link" href={`/schedule?appointment=${encodeURIComponent(c.appointment_id)}`}>{t("ai.care.appointment")}</a> : null}
      {c.invoice_id ? <a className="record-link" href={`/sales/invoices/${encodeURIComponent(c.invoice_id)}`}>{t("ai.care.invoice")}</a> : null}
    </div>
    {c.proposal_id ? <CareProposalSummary id={c.proposal_id} /> : <p>{t("ai.care.noProposal")}</p>}
    {c.status === "open" ? <PermissionGate permission="conversations.proposal.approve"><Button disabled={resolve.isPending || detail.isFetching || conflict} onClick={() => setConfirm(true)}>{t("ai.care.resolve")}</Button></PermissionGate> : null}
    {resolve.error && !(resolve.error instanceof ApiProblem && resolve.error.isConflict) ? <QueryError error={resolve.error} onRetry={() => resolve.reset()} /> : null}
    <ConfirmDialog open={confirm} onOpenChange={setConfirm} title={t("ai.care.resolveTitle")} description={t("ai.care.resolveHelp")} confirmLabel={t("ai.care.resolve")} pending={resolve.isPending} onConfirm={() => resolve.mutate(c.version, { onSuccess: () => setConfirm(false), onError: (error) => { setConfirm(false); if (error instanceof ApiProblem && error.isConflict) setConflict(true) } })} />
    <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); resolve.reset(); void detail.refetch() }} />
  </section>
}

export function CareCaseButton({ id }: { id: string }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return <><Button variant="outline" size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>{t(open ? "ai.care.hideCase" : "ai.care.viewCase")}</Button>{open ? <CustomerCareCase id={id} /> : null}</>
}
