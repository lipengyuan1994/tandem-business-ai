import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { ApiProblem } from "@/lib/problem"
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { Schemas } from "@/api/types"
import { aiKeys } from "./api"

export type ChatCard = Schemas["ChatCard"]
export type WorkItem = Schemas["WorkItem"]

export function useConfirmAction(threadId: string, proposalId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: Schemas["ChatConfirmIn"]) => unwrap(await api.POST("/api/v1/ai/copilot/threads/{thread_id}/proposals/{proposal_id}/confirm", { params: { path: { thread_id: threadId, proposal_id: proposalId } }, body })),
    onSuccess: (card) => {
      qc.setQueryData<Schemas["CopilotThreadDetailOut"]>(aiKeys.thread(threadId), (old) => old && ({ ...old, messages: old.messages.map((m) => ({ ...m, proposals: m.proposals?.map((p) => p.id === card.id ? card : p) })) }))
    },
    onSettled: () => Promise.all([qc.invalidateQueries({ queryKey: aiKeys.thread(threadId) }), qc.invalidateQueries({ queryKey: ["approvals"] }), qc.invalidateQueries({ queryKey: ["ai", "work-queue"] })]),
  })
}

export function useStopMail(id: string) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/notifications/outbox/{outbox_id}/stop", { params: { path: { outbox_id: id } } })),
    // These callbacks survive row unmount during the refresh (per-call callbacks do not).
    onSuccess: () => toast.success(t("ai.delivery.stoppedNotice")),
    onError: (error) => toast.error(t(error instanceof ApiProblem && error.code === "notifications.too_late_to_stop" ? "ai.delivery.tooLate" : error instanceof ApiProblem && error.status === 403 ? "ai.delivery.noPermission" : "ai.delivery.stopError")),
    onSettled: () => Promise.all([qc.invalidateQueries({ queryKey: ["ai"] }), qc.invalidateQueries({ queryKey: ["approvals"] }), qc.invalidateQueries({ queryKey: ["notifications"] })]),
  })
}

export function useWorkQueue(exceptions: boolean) {
  return useInfiniteQuery({
    queryKey: ["ai", exceptions ? "exceptions" : "work-queue"],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      const params = { query: { cursor: pageParam, limit: 50 } }
      // Keep the paused field typed on both branches.
      if (exceptions) return unwrap(await api.GET("/api/v1/ai/exceptions", { params }))
      return { ...unwrap(await api.GET("/api/v1/ai/work-queue", { params })), paused: false }
    },
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 15_000,
  })
}
