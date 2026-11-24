import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { Button } from "@/components/ui/button"
import { toastError } from "@/lib/errors"
import { useResolveDelivery } from "../hooks"

type Resolution = "delivered" | "retry"

/** For a message whose delivery is unknown: record that it arrived, or send it again. Sending again can duplicate it. */
export function ResolveDelivery({ proposalId, outboxId }: { proposalId: string; outboxId: string }) {
  const { t } = useTranslation()
  const resolve = useResolveDelivery(proposalId)
  const [confirm, setConfirm] = useState<Resolution | null>(null)
  return (
    <span className="flex flex-wrap gap-1">
      <Button size="sm" variant="outline" disabled={resolve.isPending} onClick={() => setConfirm("delivered")}>{t("approvals.resolve.delivered")}</Button>
      <Button size="sm" variant="outline" disabled={resolve.isPending} onClick={() => setConfirm("retry")}>{t("approvals.resolve.retry")}</Button>
      <ConfirmDialog open={confirm !== null} onOpenChange={(open) => !open && setConfirm(null)} pending={resolve.isPending}
        title={t(`approvals.resolve.${confirm ?? "delivered"}Title`)} description={t(`approvals.resolve.${confirm ?? "delivered"}Help`)} confirmLabel={t(`approvals.resolve.${confirm ?? "delivered"}`)}
        onConfirm={() => confirm && resolve.mutate({ id: outboxId, resolution: confirm }, {
          onSuccess: () => { toast.success(t(`approvals.resolve.${confirm}Done`)); setConfirm(null) },
          onError: (e) => { setConfirm(null); toastError(e) },
        })} />
    </span>
  )
}
