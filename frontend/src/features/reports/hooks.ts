import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import { useSession } from "@/app/session-context"
import { REPORT_PERMISSIONS, rangeQuery, type Period, type RangeInput, type ReportKind } from "./model"

function useAllowed(kind: ReportKind): boolean {
  const { permissions } = useSession()
  return REPORT_PERMISSIONS[kind].every((p) => permissions.has(p))
}
const options = { placeholderData: keepPreviousData, staleTime: 60_000 } as const

export function useSalesReport(range: RangeInput, groupBy: "period" | "customer" | "item", period: Period) {
  const enabled = useAllowed("sales")
  const query = { ...rangeQuery(range), group_by: groupBy, period }
  return useQuery({ ...options, enabled, queryKey: ["reports", "sales", query],
    queryFn: async () => unwrap(await api.GET("/api/v1/sales/reports/sales", { params: { query } })) })
}
export function useMarginReport(range: RangeInput, groupBy: "item" | "kind") {
  const enabled = useAllowed("margin")
  const query = { ...rangeQuery(range), group_by: groupBy }
  return useQuery({ ...options, enabled, queryKey: ["reports", "margin", query],
    queryFn: async () => unwrap(await api.GET("/api/v1/sales/reports/margin", { params: { query } })) })
}
export function useStockValueReport(asOf: string, groupBy: "item" | "location") {
  const enabled = useAllowed("stock")
  const query = { as_of: asOf || undefined, group_by: groupBy }
  return useQuery({ ...options, enabled, queryKey: ["reports", "stock", query],
    queryFn: async () => unwrap(await api.GET("/api/v1/inventory/reports/stock-value", { params: { query } })) })
}
export function useSpendReport(range: RangeInput, groupBy: "supplier" | "period", period: Period) {
  const enabled = useAllowed("spend")
  const query = { ...rangeQuery(range), group_by: groupBy, period }
  return useQuery({ ...options, enabled, queryKey: ["reports", "spend", query],
    queryFn: async () => unwrap(await api.GET("/api/v1/purchasing/reports/spend", { params: { query } })) })
}
