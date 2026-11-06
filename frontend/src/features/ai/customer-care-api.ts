import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"

export const careCaseKey = (id: string) => ["ai", "customer-care", "case", id] as const
export function useCareCase(id: string) {
  return useQuery({ queryKey: careCaseKey(id), queryFn: async () => unwrap(await api.GET("/api/v1/ai/customer-care/cases/{case_id}", { params: { path: { case_id: id } } })) })
}
export function useResolveCareCase(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: async (version: number) => unwrap(await api.POST("/api/v1/ai/customer-care/cases/{case_id}/resolve", { params: { path: { case_id: id } }, body: { version } })),
    onSuccess: (saved) => { qc.setQueryData(careCaseKey(id), saved); void qc.invalidateQueries({ queryKey: ["ai", "work-queue"] }); void qc.invalidateQueries({ queryKey: ["ai", "team"] }) },
  })
}
