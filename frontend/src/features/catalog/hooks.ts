import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { CatalogItemCreate, Schemas } from "@/api/types"

export const catalogKeys = {
  all: ["catalog"] as const,
  items: (kind: string, q: string) => ["catalog", "items", kind, q] as const,
}

export function useCatalogItems(kind: "service" | "product" | undefined, q = "") {
  return useQuery({
    queryKey: catalogKeys.items(kind ?? "all", q),
    queryFn: async () => unwrap(await api.GET("/api/v1/catalog/items", { params: { query: { kind, q: q || undefined, limit: 100 } } })),
  })
}

export function useCreateItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: CatalogItemCreate) => unwrap(await api.POST("/api/v1/catalog/items", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: catalogKeys.all }),
  })
}

export function useUpdateItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: Schemas["CatalogItemUpdate"] }) => unwrap(await api.PATCH("/api/v1/catalog/items/{item_id}", { params: { path: { item_id: id } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: catalogKeys.all }),
  })
}

export function useServiceParts(serviceId: string) {
  return useQuery({
    queryKey: ["catalog", "parts", serviceId],
    queryFn: async () => unwrap(await api.GET("/api/v1/catalog/items/{item_id}/parts", { params: { path: { item_id: serviceId } } })),
  })
}

/** Replaces the whole list of stock items a service uses up; completing an appointment consumes them. */
export function useReplaceParts(serviceId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (parts: { item_id: string; quantity: string }[]) =>
      unwrap(await api.PUT("/api/v1/catalog/items/{item_id}/parts", { params: { path: { item_id: serviceId } }, body: parts })),
    onSuccess: () => qc.invalidateQueries({ queryKey: catalogKeys.all }),
  })
}

export function useArchiveItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, version }: { id: string; version: number }) =>
      unwrap(await api.POST("/api/v1/catalog/items/{item_id}/archive", { params: { path: { item_id: id } }, body: { version } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: catalogKeys.all }),
  })
}
