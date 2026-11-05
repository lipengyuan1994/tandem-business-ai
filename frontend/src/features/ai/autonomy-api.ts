import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { Schemas } from "@/api/types"

export type AutonomyRule = Schemas["RuleOut"]
export type RuleInput = Schemas["RulePut"]
export type ActivityEntry = Schemas["ActivityOut"]
export const rulesKey = ["ai", "autonomy", "rules"] as const
export const activityKey = ["ai", "autonomy", "activity"] as const

export function useAutonomyRules() {
  return useQuery({ queryKey: rulesKey, queryFn: async () => unwrap(await api.GET("/api/v1/ai/autonomy/rules")) })
}

export function useSaveAutonomyRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: RuleInput) => unwrap(await api.PUT("/api/v1/ai/autonomy/rules", { body })),
    onSuccess: (saved) => {
      // Use the saved state: the server forces every new rule to start in shadow mode.
      qc.setQueryData<Schemas["RulesOut"]>(rulesKey, (old) => old && ({ ...old, items: old.items.map((r) => r.action_type === saved.action_type ? saved : r) }))
      void qc.invalidateQueries({ queryKey: activityKey })
    },
  })
}

export function useAutonomyPause() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (paused: boolean) => unwrap(await api.POST(paused ? "/api/v1/ai/autonomy/pause" : "/api/v1/ai/autonomy/resume")),
    onSuccess: (saved) => {
      qc.setQueryData<Schemas["RulesOut"]>(rulesKey, (old) => old && ({ ...old, paused: saved.paused }))
      void qc.invalidateQueries({ queryKey: ["org", "settings"] })
    },
  })
}

export function useAutonomyActivity() {
  return useInfiniteQuery({
    queryKey: activityKey,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => unwrap(await api.GET("/api/v1/ai/autonomy/activity", { params: { query: { cursor: pageParam, limit: 50 } } })),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 15_000,
  })
}
