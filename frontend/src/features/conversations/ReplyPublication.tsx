import { toast } from "sonner"
import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { api, unwrap } from "@/api/client"
import type { Schemas } from "@/api/types"
import { useSession } from "@/app/session-context"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { Button } from "@/components/ui/button"
import { DeliveryBadge } from "@/features/ai/DeliveryStatus"
import { useClock } from "@/features/ai/useClock"
import { ApiProblem } from "@/lib/problem"

export function ScheduledReply({ id, sendAt, stoppable = true }: { id: string; sendAt?: string | null; stoppable?: boolean }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const now = useClock()
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState(false)
  const errorText = (error: unknown) => t(error instanceof ApiProblem && error.code === "conversations.too_late_to_stop" ? "inbox.ai.tooLate" : error instanceof ApiProblem && error.status === 403 ? "inbox.ai.noStopPermission" : "inbox.ai.stopError")
  const stop = useMutation({ onError: (error) => toast.error(errorText(error)), onSuccess: () => toast.success(t("inbox.ai.stoppedNotice")), mutationFn: async () => unwrap(await api.POST("/api/v1/conversations/messages/{message_id}/stop", { params: { path: { message_id: id } } })), onSettled: () => Promise.all([qc.invalidateQueries({ queryKey: ["conversations"] }), qc.invalidateQueries({ queryKey: ["ai"] }), qc.invalidateQueries({ queryKey: ["approvals"] })]) })
  const seconds = sendAt ? Math.max(0, Math.ceil((Date.parse(sendAt) - now) / 1000)) : null
  const tooLate = stop.error instanceof ApiProblem && stop.error.code === "conversations.too_late_to_stop"
  if (stop.isSuccess) return <DeliveryBadge state="stopped" />
  return <div className="space-y-2 text-sm">
    <span className="tabular-nums">{seconds === null ? t("ai.delivery.withheld") : t("ai.delivery.countdown", { time: `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` })}</span>
    {stoppable && (permissions.has("conversations.proposal.approve") || permissions.has("org.settings.write")) ? <Button type="button" className="ml-2" variant="outline" size="sm" disabled={stop.isPending || tooLate} onClick={() => setConfirm(true)}>{t("ai.delivery.stop")}</Button> : null}
    {stop.error ? <p role="alert">{errorText(stop.error)}</p> : null}
    <ConfirmDialog open={confirm} onOpenChange={setConfirm} title={t("inbox.ai.stopTitle")} description={t("inbox.ai.stopHelp")} confirmLabel={t("inbox.ai.confirmStop")} destructive pending={stop.isPending} onConfirm={() => stop.mutate(undefined, { onSettled: () => setConfirm(false) })} />
  </div>
}
export function ReplyPublication({ message }: { message: Schemas["MessageOut"] }) {
  const { t } = useTranslation()
  if (message.publication_state === "withheld") return <ScheduledReply id={message.id} sendAt={message.publish_at} stoppable={!!message.stop_url} />
  if (message.publication_state === "stopped") return <DeliveryBadge state="stopped" />
  if (message.publication_state === "cancelled") return <p className="font-medium">{t("inbox.ai.cancelled", { reason: t(`ai.activity.reasons.${message.cancellation_reason}`, { defaultValue: t("ai.activity.unknownReason") }) })}</p>
  return null
}
