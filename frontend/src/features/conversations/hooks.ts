import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { Thread } from "@/api/types"

export type InboxTab = "waiting_on_staff" | "waiting_on_customer" | "closed"

export const conversationKeys = {
  all: ["conversations"] as const,
  list: (tab: InboxTab) => ["conversations", "list", tab] as const,
  thread: (id: string) => ["conversations", "thread", id] as const,
}

export function useConversations(tab: InboxTab) {
  return useQuery({
    queryKey: conversationKeys.list(tab),
    queryFn: async () => unwrap(await api.GET("/api/v1/conversations", { params: { query: { status: tab, limit: 50 } } })),
    refetchInterval: 15_000,
  })
}

export function useThread(id: string | undefined) {
  return useQuery({
    queryKey: conversationKeys.thread(id ?? ""),
    enabled: !!id,
    queryFn: async () => unwrap(await api.GET("/api/v1/conversations/{conversation_id}", { params: { path: { conversation_id: id! } } })),
    refetchInterval: 10_000,
  })
}

function useConversationMutation<TVars, TOut>(fn: (vars: TVars) => Promise<TOut>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: conversationKeys.all }), qc.invalidateQueries({ queryKey: ["crm"] })]) })
}

export function useSendMessage(id: string) {
  return useConversationMutation(async (body: { direction: "outbound" | "internal"; body: string }) =>
    unwrap(await api.POST("/api/v1/conversations/{conversation_id}/messages", { params: { path: { conversation_id: id } }, body })))
}

export function useUpdateConversation(id: string) {
  return useConversationMutation(async (body: { version: number; customer_id?: string | null; status?: Thread["status"] | null }) =>
    unwrap(await api.PATCH("/api/v1/conversations/{conversation_id}", { params: { path: { conversation_id: id } }, body })))
}

export function useOwnership(id: string) {
  return useConversationMutation(async (mode: "auto" | "human") => {
    const params = { path: { conversation_id: id } }
    return mode === "human" ? unwrap(await api.POST("/api/v1/conversations/{conversation_id}/take-over", { params })) : unwrap(await api.POST("/api/v1/conversations/{conversation_id}/hand-back", { params }))
  })
}
