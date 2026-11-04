import { useEffect, useState } from "react"
import type { Schemas } from "@/api/types"
import { call, client } from "./api"

export function useSupplierRequest(token: string) {
  const [data, setData] = useState<Schemas["PublicQuoteRequest"] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let alive = true
    call(client.GET("/api/v1/public/quotes/{token}", { params: { path: { token } } })).then((d) => { if (alive) { setData(d); setError(null) } }, (e: unknown) => { if (alive) setError(e) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [token, attempt])
  return { data, error, loading, retry: () => { setLoading(true); setAttempt(attempt + 1) } }
}
export function submitSupplierQuote(token: string, body: Schemas["PublicQuoteInput"] | Schemas["DeclineQuote"]) {
  return call(client.POST("/api/v1/public/quotes/{token}", { params: { path: { token } }, body }))
}
