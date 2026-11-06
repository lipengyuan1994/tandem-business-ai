import { Link } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { PreviewDiff } from "@/features/approvals/components/PreviewDiff"
import { ApiProblem } from "@/lib/problem"
import { useConfirmAction, type ChatCard } from "./actions-api"
import { CardReplyGrounding } from "./ReplyGrounding"
import { ProposalDelivery } from "./DeliveryStatus"

function confirmReason(code: string | null | undefined) {
  if (code?.endsWith("changed") || code?.endsWith("version_conflict")) return "changed"
  switch (code) {
    case "approvals.second_person_required": return "secondPerson"
    case "approvals.chat_confirm_limit": return "dailyLimit"
    case "approvals.not_authorized_to_approve": return "noPermission"
    case "approvals.expired": return "expired"
    case "approvals.not_pending": return "notPending"
    case "purchasing.supplier_inactive": return "supplierInactive"
    case "purchasing.supplier_email_required": return "supplierEmail"
    case "purchasing.po_already_sent": return "alreadySent"
    default: return "unavailable"
  }
}

export function ActionCard({ card, threadId, onReload }: { card: ChatCard; threadId: string; onReload: () => void }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const confirm = useConfirmAction(threadId, card.id)
  const [collapsed, setCollapsed] = useState(false)
  const result = card.result ?? {}
  const errorCode = confirm.error instanceof ApiProblem ? confirm.error.code : null
  const reason = errorCode ?? card.cannot_confirm_reason
  const canConfirm = card.can_confirm && !confirm.error
  const changed = confirmReason(reason) === "changed"
  return <Card className="mt-3 gap-3 py-3">
    <article aria-label={card.title}>
      <CardHeader className="gap-2 px-3"><CardTitle className="text-sm">{card.title}</CardTitle><StatusBadge status={card.status} /></CardHeader>
      <CardContent className="mt-3 space-y-3 px-3">
        {collapsed ? <Button size="sm" variant="ghost" onClick={() => setCollapsed(false)}>{t("ai.cards.show")}</Button> : <>
          <PreviewDiff lines={card.preview} />
          {card.action_type === "conversations.send_reply" ? <CardReplyGrounding proposalId={card.id} revisionId={card.revision_id} /> : null}
          <ProposalDelivery proposalId={card.id} state={card.delivery_state} />
          {card.status === "succeeded" && result.purchase_order_id && permissions.has("purchasing.po.read") ? <Link className="record-link block" to="/purchasing/purchase-orders/$id" params={{ id: result.purchase_order_id }}>{t(`ai.cards.results.${card.action_type === "purchasing.send_po" ? "sent" : card.action_type === "purchasing.approve_po" ? "approved" : "created"}`)}</Link> : null}
          {card.status === "succeeded" && result.reorder_draft_id && permissions.has("inventory.reorder.read") ? <a className="record-link block" href={`/api/v1/inventory/reorder-drafts/${result.reorder_draft_id}`}>{t("ai.activity.draftCreated")}</a> : null}
          {!canConfirm && (card.status === "pending_review" || confirm.error) ? <p role={confirm.error ? "alert" : undefined}>{t(`ai.cards.reasons.${confirmReason(reason)}`)}</p> : null}
          <div className="flex flex-wrap gap-2">
            {canConfirm ? <Button size="sm" disabled={confirm.isPending} onClick={() => confirm.mutate({ revision_id: card.revision_id, payload_hash: card.payload_hash })}>{t("common.confirm")}</Button> : null}
            <Link className="record-link inline-flex min-h-9 items-center" to="/approvals/$proposalId" params={{ proposalId: card.id }}>{t(canConfirm ? "ai.cards.edit" : "ai.cards.review")}</Link>
            {changed || confirm.error ? <Button size="sm" variant="outline" disabled={confirm.isPending} onClick={() => { confirm.reset(); onReload() }}>{t("ai.cards.reload")}</Button> : null}
            <Button size="sm" variant="ghost" onClick={() => setCollapsed(true)}>{t("ai.cards.notNow")}</Button>
          </div>
        </>}
      </CardContent>
    </article>
  </Card>
}
