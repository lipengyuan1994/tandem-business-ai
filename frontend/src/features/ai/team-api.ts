import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { Schemas } from "@/api/types"
import { useSession } from "@/app/session-context"
import { ApiProblem } from "@/lib/problem"

export type Agent = Schemas["AgentOut"]
export type TeamItem = Schemas["TeamActivityItem"]
export const teamKey = ["ai", "team"] as const

export function useAgents(enabled = true) {
  const { permissions } = useSession()
  return useQuery({ queryKey: teamKey, enabled: enabled && permissions.has("approvals.proposal.read"), refetchInterval: 15_000,
    queryFn: async () => unwrap(await api.GET("/api/v1/ai/agents")),
  })
}

export function usePatchAgent(key: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: Schemas["AgentPatch"]) => unwrap(await api.PATCH("/api/v1/ai/agents/{key}", { params: { path: { key } }, body })),
    onSuccess: (saved) => {
      qc.setQueryData<Schemas["AgentsOut"]>(teamKey, (old) => old && ({ items: old.items.map((item) => item.key === key ? saved : item) }))
      return qc.invalidateQueries({ queryKey: ["ai"] })
    },
    onError: async (error) => { if (error instanceof ApiProblem && error.isConflict) await qc.invalidateQueries({ queryKey: teamKey }) },
  })
}

export function useTeamActivity(key: string) {
  return useInfiniteQuery({ queryKey: ["ai", "team-activity", key], enabled: !!key,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => unwrap(await api.GET("/api/v1/ai/agents/{key}/activity", { params: { path: { key }, query: { cursor: pageParam, limit: 50 } } })),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 15_000,
  })
}

export function useChain(correlation_id: string) {
  return useQuery({ queryKey: ["ai", "chain", correlation_id],
    queryFn: async () => unwrap(await api.GET("/api/v1/ai/chains/{correlation_id}", { params: { path: { correlation_id } } })),
    refetchInterval: 15_000,
  })
}
