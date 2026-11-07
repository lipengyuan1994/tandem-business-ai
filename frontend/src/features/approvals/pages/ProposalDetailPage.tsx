import { QueryError } from "@/components/common/QueryError"
import { useParams } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { DeliveryBadge, ScheduledMail } from "@/features/ai/DeliveryStatus"
import { CustomerCareProposal } from "@/features/ai/CustomerCareProposal"
import { ReplyGrounding } from "@/features/ai/ReplyGrounding"
import { AiBadge } from "@/features/ai/AiBadge"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { ApiProblem } from "@/lib/problem"
import { toastError } from "@/lib/errors"
import { EvidenceList } from "../components/EvidenceList"
import { PreviewDiff } from "../components/PreviewDiff"
import { ACTION_FORMS } from "../components/forms"
import { RevisionEditor } from "../components/RevisionEditor"
import { useActionTypes, useCancel, useDecide, useProposal, useProposalOutbox, useRevise } from "../hooks"

export function ProposalDetailPage() {
  const { t } = useTranslation()
  const { proposalId } = useParams({ from: "/app/approvals/$proposalId" })
  const { businessTimeZone, userId } = useSession()
  const proposal = useProposal(proposalId)
  const types = useActionTypes()
  const decide = useDecide(proposalId)
  const revise = useRevise(proposalId)
  const cancel = useCancel(proposalId)
  const [editing, setEditing] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [comment, setComment] = useState("")
  const p = proposal.data
  const outbox = useProposalOutbox(proposalId, !!p && p.status !== "pending_review")

  if (proposal.error) return <QueryError error={proposal.error} onRetry={() => proposal.refetch()} notFound={t("approvals.notFound")} />
  if (proposal.isPending) return <Skeleton className="h-96 w-full" />
  if (!p) return <EmptyState title={t("approvals.notFound")} />
  const rev = p.current_revision
  const ActionForm = ACTION_FORMS[p.action_type]
  const effect = rev.preview.map((l) => l.after).filter(Boolean).join(" · ")
  const onDecisionError = (e: unknown) => {
    if (e instanceof ApiProblem && e.code === "approvals.revision_changed") void proposal.refetch()
    toastError(e)
  }
  const submitRevision = (payload: Record<string, unknown>) =>
    revise.mutate({ base_revision_id: rev.id, payload }, { onSuccess: () => setEditing(false), onError: onDecisionError })

  return (
    <>
      <PageHeader
        title={p.title}
        description={t("approvals.detailSubtitle", { type: p.action_type })}
        actions={
          <div className="flex items-center gap-2">
            <AiBadge originKind={p.origin_kind} />
            <StatusBadge status={p.status} /><DeliveryBadge state={p.delivery_state} />
          </div>
        }
      />
      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <div className="space-y-6">
          <section aria-labelledby="changes-h" className="space-y-2">
            <h2 id="changes-h" className="font-medium">{t("approvals.changes", { n: rev.revision_no })}</h2>
            {editing && ActionForm ? (
              <ActionForm payload={rev.payload} pending={revise.isPending} onCancel={() => setEditing(false)} onSubmit={submitRevision} />
            ) : editing ? (
              <RevisionEditor
                schema={types.data?.find((x) => x.action_type === p.action_type)?.payload_schema as never}
                payload={rev.payload}
                pending={revise.isPending}
                onCancel={() => setEditing(false)}
                onSubmit={submitRevision}
              />
            ) : (
              p.action_type === "customer_care.send_message" ? <CustomerCareProposal payload={rev.payload} /> : <PreviewDiff lines={rev.preview} />
            )}
          </section>

          {p.action_type === "conversations.send_reply" ? <>
            {p.failure_code?.startsWith("guardrail_") ? <p role="status">{t(`ai.activity.reasons.${p.failure_code}`)}</p> : null}
            <ReplyGrounding payload={rev.payload} />
          </> : null}

          {p.status === "pending_review" && !editing ? (
            <div className="flex flex-wrap gap-2">
              {p.can_decide ? (
                <>
                  <Button className="h-auto max-w-full whitespace-normal text-left [overflow-wrap:anywhere]" disabled={decide.isPending} onClick={() => decide.mutate({ decision: "approve", revision_id: rev.id, payload_hash: rev.payload_hash }, { onError: onDecisionError })}>
                    {t("approvals.approveEffect", { effect })}
                  </Button>
                  <Button variant="outline" onClick={() => setRejecting(true)}>{t("approvals.reject")}</Button>
                </>
              ) : null}
              {p.can_decide || p.origin_user_id === userId ? <Button variant="ghost" onClick={() => setEditing(true)}>{t("approvals.edit.open")}</Button> : null}
              {p.origin_user_id === userId ? <Button variant="ghost" onClick={() => cancel.mutate(undefined, { onError: (e) => toastError(e) })}>{t("approvals.cancel")}</Button> : null}
            </div>
          ) : null}

          {p.status === "failed" ? <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">{t("approvals.failed", { code: p.failure_code })}</p> : null}

          {outbox.error ? <QueryError error={outbox.error} onRetry={() => outbox.refetch()} /> : null}
          {types.error && editing ? <QueryError error={types.error} onRetry={() => types.refetch()} /> : null}
          {p.status !== "pending_review" && (outbox.data?.length ?? 0) > 0 ? (
            <section aria-labelledby="delivery-h" className="space-y-2">
              <h2 id="delivery-h" className="font-medium">{t("approvals.deliveries")}</h2>
              <ul className="space-y-1">
                {outbox.data!.map((m) => (
                  <li key={m.id} className="flex items-center gap-2 text-sm">
                    <DeliveryBadge state={m.status === "pending" ? "scheduled" : m.status === "cancelled" ? "stopped" : m.status} />
                    {m.channel === "simulated" && m.status === "delivered" ? <StatusBadge status="simulated" /> : null}
                    {m.status === "pending" ? <ScheduledMail id={m.id} sendAt={m.not_before} /> : null}
                    <span>{t(`approvals.channels.${m.channel}`)}</span>
                    {m.status === "unknown" ? <span className="text-destructive">{t("approvals.uncertain")}</span> : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>

        <aside className="space-y-4">
          <Card>
            <CardHeader><CardTitle className="text-sm">{p.origin_kind === "assistant" ? t("ai.evidence.title") : t("approvals.evidence")}</CardTitle></CardHeader>
            <CardContent>
              {p.origin_kind === "assistant" ? <p className="mb-2 text-xs text-muted-foreground">{t("ai.evidence.note")}</p> : null}
              <EvidenceList evidence={rev.evidence} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm">{t("approvals.history")}</CardTitle></CardHeader>
            <CardContent>
              <ol className="space-y-2 text-sm">
                {p.revisions.map((r) => (
                  <li key={r.id}>{t("approvals.revisionLine", { n: r.revision_no, who: t(`approvals.by.${r.created_by_kind}`) })} · <LocalTime iso={r.created_at} timeZone={businessTimeZone} pattern="d MMM HH:mm" /></li>
                ))}
                {p.decisions.map((d) => (
                  <li key={d.id}>{d.decided_by_kind === "policy" ? t("approvals.policyDecision", { version: d.rule_version, who: d.granted_by_name || d.granted_by || t("ai.activity.unknownGrantor") }) : t(`approvals.decisionLine.${d.decision}`, { who: d.decided_by_name || d.decided_by || t("approvals.unknownDecider") })}{d.comment ? `: “${d.comment}”` : ""}</li>
                ))}
                {p.attempts.map((a) => (
                  <li key={a.id}>{t("approvals.attemptLine")} <StatusBadge status={a.status === "running" ? "executing" : a.status} /></li>
                ))}
              </ol>
              <p className="mt-3 text-xs text-muted-foreground">{t("approvals.expires")} <LocalTime iso={p.expires_at} timeZone={businessTimeZone} pattern="d MMM HH:mm" /></p>
            </CardContent>
          </Card>
        </aside>
      </div>

      <ConfirmDialog
        open={rejecting}
        onOpenChange={setRejecting}
        title={t("approvals.rejectTitle")}
        confirmLabel={t("approvals.reject")}
        destructive
        pending={decide.isPending}
        onConfirm={() => decide.mutate({ decision: "reject", revision_id: rev.id, payload_hash: rev.payload_hash, comment: comment || null },
          { onSuccess: () => setRejecting(false), onError: onDecisionError })}
      >
        <div className="space-y-1">
          <Label htmlFor="reject-comment">{t("approvals.comment")}</Label>
          <Textarea dictation={{ context: "note" }} id="reject-comment" value={comment} onChange={(e) => setComment(e.target.value)} />
        </div>
      </ConfirmDialog>
    </>
  )
}
