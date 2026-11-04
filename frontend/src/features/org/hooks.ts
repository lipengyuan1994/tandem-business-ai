import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { BusinessSettings, OpeningHoursItem, Schemas } from "@/api/types"

export const orgKeys = {
  business: ["org", "business"] as const,
  locations: ["org", "locations"] as const,
  hours: (id: string) => ["org", "hours", id] as const,
  settings: ["org", "settings"] as const,
}

export function useBusiness() {
  return useQuery({ queryKey: orgKeys.business, queryFn: async () => unwrap(await api.GET("/api/v1/org/business")) })
}

export function useUpdateBusiness() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: Schemas["BusinessUpdate"]) => unwrap(await api.PATCH("/api/v1/org/business", { body })),
    onSuccess: (b) => qc.setQueryData(orgKeys.business, b),
  })
}

export function useLocations() {
  return useQuery({ queryKey: orgKeys.locations, queryFn: async () => unwrap(await api.GET("/api/v1/org/locations")) })
}

export function useCreateLocation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: Schemas["LocationCreate"]) => unwrap(await api.POST("/api/v1/org/locations", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: orgKeys.locations }),
  })
}

export function useOpeningHours(locationId: string | undefined) {
  return useQuery({
    queryKey: orgKeys.hours(locationId ?? "none"),
    enabled: !!locationId,
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/org/locations/{location_id}/opening-hours", { params: { path: { location_id: locationId! } } })),
  })
}

export function useReplaceHours(locationId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (items: OpeningHoursItem[]) =>
      unwrap(await api.PUT("/api/v1/org/locations/{location_id}/opening-hours", { params: { path: { location_id: locationId } }, body: { items } })),
    onSuccess: (rows) => qc.setQueryData(orgKeys.hours(locationId), rows),
  })
}

export function useSettings() {
  return useQuery({ queryKey: orgKeys.settings, queryFn: async () => unwrap(await api.GET("/api/v1/org/settings")) })
}

export function useUpdateSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: { version: number; settings: BusinessSettings }) => unwrap(await api.PATCH("/api/v1/org/settings", { body })),
    onSuccess: (s) => qc.setQueryData(orgKeys.settings, s),
  })
}
