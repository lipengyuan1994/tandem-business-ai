import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { Clause } from "@/api/types"

export const marketingKeys = {
  all: ["marketing"] as const,
  campaigns: ["marketing", "campaigns"] as const,
  campaign: (id: string) => ["marketing", "campaign", id] as const,
  segments: ["marketing", "segments"] as const,
  preview: (segmentId: string, offset: number) => ["marketing", "preview", segmentId, offset] as const,
  suppressions: ["marketing", "suppressions"] as const,
}

export function useCampaigns() {
  return useQuery({ queryKey: marketingKeys.campaigns, queryFn: async () => unwrap(await api.GET("/api/v1/marketing/campaigns")) })
}

export function useCampaign(id: string) {
  return useQuery({
    queryKey: marketingKeys.campaign(id),
    queryFn: async () => unwrap(await api.GET("/api/v1/marketing/campaigns/{campaign_id}", { params: { path: { campaign_id: id } } })),
  })
}

export function useSegments() {
  return useQuery({ queryKey: marketingKeys.segments, queryFn: async () => unwrap(await api.GET("/api/v1/marketing/segments")) })
}

export function useAudiencePreview(segmentId: string | undefined, offset = 0) {
  return useQuery({
    queryKey: marketingKeys.preview(segmentId ?? "", offset),
    enabled: !!segmentId,
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/marketing/segments/{segment_id}/preview", { params: { path: { segment_id: segmentId! }, query: { offset, limit: 10 } } })),
  })
}

export function useSuppressions() {
  return useQuery({ queryKey: marketingKeys.suppressions, queryFn: async () => unwrap(await api.GET("/api/v1/marketing/suppressions")) })
}

function useMarketingMutation<TVars, TOut>(fn: (vars: TVars) => Promise<TOut>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: marketingKeys.all }), qc.invalidateQueries({ queryKey: ["approvals"] })]) })
}

/** Saves the audience, then the campaign draft with its first message. */
export function useCreateCampaign() {
  return useMarketingMutation(async (v: { name: string; purpose: string | null; clauses: Clause[]; subject: string; body_markdown: string }) => {
    const segment = unwrap(await api.POST("/api/v1/marketing/segments", { body: { name: v.name, definition: { all: v.clauses } } }))
    return unwrap(await api.POST("/api/v1/marketing/campaigns", {
      body: { name: v.name, purpose: v.purpose, segment_id: segment.id, subject: v.subject, body_markdown: v.body_markdown },
    }))
  })
}

export function useAddRevision(id: string) {
  return useMarketingMutation(async (body: { subject: string; body_markdown: string }) =>
    unwrap(await api.POST("/api/v1/marketing/campaigns/{campaign_id}/revisions", { params: { path: { campaign_id: id } }, body })))
}

export function useSubmitCampaign(id: string) {
  return useMarketingMutation(async () => unwrap(await api.POST("/api/v1/marketing/campaigns/{campaign_id}/submit", { params: { path: { campaign_id: id } } })))
}

export function useCancelCampaign(id: string) {
  return useMarketingMutation(async () => unwrap(await api.POST("/api/v1/marketing/campaigns/{campaign_id}/cancel", { params: { path: { campaign_id: id } } })))
}

export function useAddSuppression() {
  return useMarketingMutation(async (body: { value: string; reason: "manual" | "bounced" | "complaint" }) =>
    unwrap(await api.POST("/api/v1/marketing/suppressions", { body })))
}
