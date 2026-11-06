import { useTranslation } from "react-i18next"
import type { Thread } from "@/api/types"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { toastError } from "@/lib/errors"
import { useOwnership } from "./hooks"

export function OwnershipControls({ thread }: { thread: Thread }) {
  const { t } = useTranslation()
  const ownership = useOwnership(thread.id)
  return <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
    <Badge variant={thread.ai_mode === "human" ? "secondary" : "outline"}>{t(thread.ai_mode === "human" ? "inbox.ai.human" : "inbox.ai.auto")}</Badge>
    <PermissionGate permission="conversations.thread.write"><Button size="sm" variant="outline" disabled={ownership.isPending || thread.status === "closed"} onClick={() => ownership.mutate(thread.ai_mode === "human" ? "auto" : "human", { onError: (e) => toastError(e) })}>{t(thread.ai_mode === "human" ? "inbox.ai.handBack" : "inbox.ai.takeOver")}</Button></PermissionGate>
    {thread.handoff_requested_at ? <p role="status" className="rounded-md border border-status-pending bg-status-pending/10 px-3 py-2 font-medium">{t("inbox.ai.handoff")}</p> : null}
  </div>
}
