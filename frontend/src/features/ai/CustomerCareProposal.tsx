import { useTranslation } from "react-i18next"
import { useQuery } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import { useSession } from "@/app/session-context"
import { LocalTime } from "@/components/common/LocalTime"
import { QueryError } from "@/components/common/QueryError"
import { useProposal } from "@/features/approvals/hooks"

export function CareCustomer({ id }: { id: string }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const customer = useQuery({ queryKey: ["crm", "customer", id], enabled: permissions.has("crm.customer.read"), queryFn: async () => unwrap(await api.GET("/api/v1/crm/customers/{customer_id}", { params: { path: { customer_id: id } } })) })
  return <a className="record-link" href={`/customers/${encodeURIComponent(id)}`}>{customer.data?.display_name ?? `${t("ai.care.customer")} · ${id}`}</a>
}

export function CustomerCareProposal({ payload }: { payload: Record<string, unknown> }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const text = (key: string) => typeof payload[key] === "string" ? payload[key] as string : ""
  const facts = ["appointment_id", "service_id", "service_date", "invoice_id", "source_message_id", "due_month"] as const
  const links: Record<string, string> = { appointment_id: "/schedule?appointment=", invoice_id: "/sales/invoices/" }
  return <section aria-label={t("ai.care.message")} className="space-y-3 rounded-md border p-3">
    <p className="font-medium">{t(`ai.care.kinds.${text("kind")}`, { defaultValue: t("ai.care.message") })}</p>
    {text("kind") === "complaint_reply" ? <p className="status-badge">{t("ai.care.needsPerson")}</p> : null}
    {text("customer_id") ? <p><CareCustomer id={text("customer_id")} /></p> : null}
    <dl className="space-y-2"><div><dt className="font-medium">{t("ai.care.subject")}</dt><dd>{text("subject")}</dd></div><div><dt className="font-medium">{t("ai.care.body")}</dt><dd className="whitespace-pre-wrap">{text("body")}</dd></div>
      {text("not_before") ? <div><dt className="font-medium">{t("ai.care.earliest")}</dt><dd><LocalTime iso={text("not_before")} timeZone={businessTimeZone} pattern="d MMM yyyy HH:mm" /></dd></div> : null}
    </dl>
    <h3 className="font-medium">{t("ai.care.facts")}</h3>
    <dl className="space-y-1 text-sm">{facts.filter((key) => text(key)).map((key) => <div key={key}><dt className="inline font-medium">{t(`ai.care.factsLabels.${key}`)}: </dt><dd className="inline">{links[key] ? <a className="record-link" href={`${links[key]}${encodeURIComponent(text(key))}`}>{text(key)}</a> : text(key)}</dd></div>)}</dl>
  </section>
}

export function CareProposalSummary({ id }: { id: string }) {
  const { t } = useTranslation()
  const proposal = useProposal(id)
  return <div className="space-y-2">
    {proposal.error ? <QueryError error={proposal.error} onRetry={() => proposal.refetch()} /> : proposal.data?.action_type === "customer_care.send_message" ? <CustomerCareProposal payload={proposal.data.current_revision.payload} /> : null}
    <a className="record-link" href={`/approvals/${encodeURIComponent(id)}`}>{t("ai.care.reviewProposal")}</a>
  </div>
}
