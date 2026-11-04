import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { CatalogItemCreate } from "@/api/types"

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
