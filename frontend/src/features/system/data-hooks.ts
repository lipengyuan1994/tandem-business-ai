import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"

/** Downloads the ZIP of all of the business's data. The server audits and rate-limits it. */
export function useExportData() {
  return useMutation({
    mutationFn: async () => {
      const result = await api.POST("/api/v1/admin/exports", { parseAs: "blob" })
      const blob = unwrap(result as { data?: Blob; error?: unknown; response: Response })
      const disposition = result.response.headers.get("content-disposition") ?? ""
      const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "export.zip"
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = filename
      link.click()
      URL.revokeObjectURL(url)
      return filename
    },
  })
}

export function useErasures(enabled: boolean) {
  return useQuery({ queryKey: ["admin", "erasures"], enabled, queryFn: async () => unwrap(await api.GET("/api/v1/admin/erasures")) })
}

export function useEraseCustomer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: { customer_id: string; confirm_name: string }) => unwrap(await api.POST("/api/v1/admin/erasures", { body })),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ["crm"] }), qc.invalidateQueries({ queryKey: ["admin"] })]),
  })
}
