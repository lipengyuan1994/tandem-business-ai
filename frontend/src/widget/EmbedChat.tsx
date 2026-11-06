import { useEffect } from "react"
import { call, client } from "./api"
import { Widget } from "./Widget"

export function EmbedChat({ slug }: { slug: string }) {
  useEffect(() => {
    let alive = true
    let allowed: string[] = []
    let parentOrigin: string | null = null
    let pending: MessageEvent | null = null
    void call(client.GET("/api/v1/public/{business_slug}/embed-config", { params: { path: { business_slug: slug } } })).then((r) => { if (alive) { allowed = r.allowed_origins; if (pending) receive(pending) } }, () => {})
    const receive = (event: MessageEvent) => {
      if (event.source === parent && !allowed.length) pending = event
      if (event.source !== parent || !allowed.includes(event.origin) || !event.data || typeof event.data !== "object" || Object.keys(event.data).length !== 1) return
      if (event.data.type === "smb:open") {
        parentOrigin = event.origin
        document.querySelector<HTMLElement>("main input, main button")?.focus()
        parent.postMessage({ type: "smb:resize", height: Math.min(800, document.documentElement.scrollHeight + 44) }, parentOrigin)
      }
      if (event.data.type === "smb:close") parentOrigin = null
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && parentOrigin) parent.postMessage({ type: "smb:close" }, parentOrigin)
    }
    window.addEventListener("message", receive)
    window.addEventListener("keydown", escape)
    return () => { alive = false; window.removeEventListener("message", receive); window.removeEventListener("keydown", escape) }
  }, [slug])
  return <Widget slug={slug} page="chat" compact />
}
