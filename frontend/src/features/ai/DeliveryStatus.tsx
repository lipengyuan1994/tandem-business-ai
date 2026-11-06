import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { Schemas } from "@/api/types"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { useProposalOutbox } from "@/features/approvals/hooks"
import { ApiProblem } from "@/lib/problem"
import { useClock } from "./useClock"
import { useStopMail } from "./actions-api"

type DeliveryState = Schemas["ChatCard"]["delivery_state"]
const TONES = { withheld: "pending", published: "success", none: "neutral", scheduled: "pending", sending: "approved", delivered: "success", failed: "failed", unknown: "neutral", stopped: "neutral" } as const
export function DeliveryBadge({ state }: { state: DeliveryState }) {
  const { t } = useTranslation()
  return <span className="status-badge" data-tone={TONES[state]}>{t(`ai.delivery.${state ?? "none"}`)}</span>
}

export function ScheduledMail({ id, sendAt }: { id: string; sendAt?: string | null }) {
  const { t } = useTranslation()
  const now = useClock()
  const stop = useStopMail(id)
  const [confirm, setConfirm] = useState(false)
  const [stopped, setStopped] = useState(false)
  const seconds = sendAt ? Math.max(0, Math.ceil((Date.parse(sendAt) - now) / 1000)) : null
  const tooLate = stop.error instanceof ApiProblem && stop.error.code === "notifications.too_late_to_stop"
  if (stopped) return <DeliveryBadge state="stopped" />
  return <div className="space-y-2 text-sm">
    <div className="flex flex-wrap items-center gap-2">
      <span className="tabular-nums">{seconds === null ? t("ai.delivery.scheduled") : t("ai.delivery.countdown", { time: `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` })}</span>
      <Button type="button" size="sm" variant="outline" disabled={stop.isPending || tooLate} onClick={() => setConfirm(true)}>{t("ai.delivery.stop")}</Button>
    </div>
    {stop.error ? <p role="alert">{t(tooLate ? "ai.delivery.tooLate" : stop.error instanceof ApiProblem && stop.error.status === 403 ? "ai.delivery.noPermission" : "ai.delivery.stopError")}</p> : null}
    <ConfirmDialog open={confirm} onOpenChange={setConfirm} title={t("ai.delivery.stopTitle")} description={t("ai.delivery.stopHelp")} confirmLabel={t("ai.delivery.confirmStop")} destructive pending={stop.isPending} onConfirm={() => stop.mutate(undefined, { onSuccess: () => { setStopped(true); setConfirm(false) }, onError: () => setConfirm(false) })} />
  </div>
}

/** Cards and Activity expose aggregate state; the existing scoped outbox read supplies individual stop ids/dates. */
export function ProposalDelivery({ proposalId, state }: { proposalId: string; state: DeliveryState }) {
  const tracking = state === "scheduled" || state === "sending"
  const outbox = useProposalOutbox(proposalId, tracking)
  const states = (tracking ? outbox.data : undefined)?.map((mail): DeliveryState => mail.status === "pending" ? "scheduled" : mail.status === "cancelled" ? "stopped" : mail.status) ?? []
  const current = (["unknown", "failed", "sending", "scheduled", "stopped", "delivered"] as const).find((candidate) => states.includes(candidate)) ?? state
  return <div className="space-y-2">
    <DeliveryBadge state={current} />
    {tracking && outbox.error ? <QueryError error={outbox.error} onRetry={() => outbox.refetch()} /> : null}
    {tracking ? outbox.data?.map((mail) => mail.status === "pending" ? <ScheduledMail key={mail.id} id={mail.id} sendAt={mail.not_before} /> : null) : null}
  </div>
}
