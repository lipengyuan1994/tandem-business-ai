import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { ProposalDetail } from "@/api/types"

export type QueueTab = "mine-to-decide" | "pending" | "recent"

export const approvalKeys = {
  list: (tab: QueueTab) => ["approvals", "list", tab] as const,
  detail: (id: string) => ["approvals", "detail", id] as const,
  outbox: (id: string) => ["approvals", "outbox", id] as const,
  types: ["approvals", "types"] as const,
}

const QUERY: Record<QueueTab, { can_approve?: boolean; status?: string }> = {
  "mine-to-decide": { can_approve: true },
  pending: { status: "pending_review" },
  recent: {},
}

export function useProposals(tab: QueueTab) {
  return useQuery({
    queryKey: approvalKeys.list(tab),
    queryFn: async () => unwrap(await api.GET("/api/v1/approvals/proposals", { params: { query: { ...QUERY[tab], limit: 50 } } })),
  })
}

const LIVE = new Set(["approved", "executing"])

export function useProposal(id: string) {
  return useQuery({
    queryKey: approvalKeys.detail(id),
    queryFn: async () => unwrap(await api.GET("/api/v1/approvals/proposals/{proposal_id}", { params: { path: { proposal_id: id } } })),
    refetchInterval: (q) => (q.state.data && (LIVE.has(q.state.data.status) || ["scheduled", "sending"].includes(q.state.data.delivery_state)) ? 1500 : false),
  })
}

export function useProposalOutbox(id: string, enabled: boolean) {
  return useQuery({
    queryKey: approvalKeys.outbox(id),
    enabled,
    queryFn: async () => unwrap(await api.GET("/api/v1/notifications/outbox", { params: { query: { proposal_id: id } } })),
    refetchInterval: 3000,
  })
}

export function useActionTypes() {
  return useQuery({ queryKey: approvalKeys.types, queryFn: async () => unwrap(await api.GET("/api/v1/approvals/action-types")), staleTime: 300_000 })
}

function useDetailMutation<TVars>(id: string, fn: (vars: TVars) => Promise<ProposalDetail>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (detail) => {
      qc.setQueryData(approvalKeys.detail(id), detail)
      void qc.invalidateQueries({ queryKey: ["approvals", "list"] })
    },
  })
}

export function useDecide(id: string) {
  return useDetailMutation(id, async (body: { decision: "approve" | "reject"; revision_id: string; payload_hash: string; comment?: string | null }) =>
    unwrap(await api.POST("/api/v1/approvals/proposals/{proposal_id}/decisions", { params: { path: { proposal_id: id } }, body })))
}

export function useRevise(id: string) {
  return useDetailMutation(id, async (body: { base_revision_id: string; payload: Record<string, unknown> }) =>
    unwrap(await api.POST("/api/v1/approvals/proposals/{proposal_id}/revisions", { params: { path: { proposal_id: id } }, body })))
}

export function useCancel(id: string) {
  return useDetailMutation(id, async () =>
    unwrap(await api.POST("/api/v1/approvals/proposals/{proposal_id}/cancel", { params: { path: { proposal_id: id } } })))
}
