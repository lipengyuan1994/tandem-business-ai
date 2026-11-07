import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { api, unwrap } from "@/api/client"
import { Button } from "@/components/ui/button"

function bookingLink(body: string): string | null {
  for (const match of body.matchAll(/https?:\/\/[^\s]+/g)) {
    try {
      const url = new URL(match[0])
      if (url.origin === location.origin && /^\/p\/[a-z0-9-]+\/bookings$/.test(url.pathname) && /^#t=[A-Za-z0-9_-]{43}$/.test(url.hash)) return url.href
    } catch { /* plain text only */ }
  }
  return null
}

export function SimulatedEmails() {
  const { t } = useTranslation()
  const list = useQuery({ queryKey: ["notifications", "simulated"], queryFn: async () => unwrap(await api.GET("/api/v1/notifications/simulated-emails")) })
  return <section aria-labelledby="simulated-heading" className="space-y-3 rounded border p-4">
    <h2 id="simulated-heading" className="font-medium">{t("website.simulated")}</h2><p>{t("website.simulatedHelp")}</p>
    <Button onClick={() => void list.refetch()} disabled={list.isFetching}>{t("website.refresh")}</Button>
    {list.isPending ? <p role="status">{t("common.loading")}</p> : null}
    {list.error ? <p role="alert">{t("website.mailError")}</p> : null}
    {list.data?.length === 0 ? <p role="status">{t("website.mailEmpty")}</p> : null}
    <ul className="space-y-4">{list.data?.map((row) => {
      const link = bookingLink(row.body)
      return <li className="space-y-1 border-t pt-2" key={row.id}><h3 className="font-medium">{row.subject}</h3><p>{row.recipient} · {new Date(row.created_at).toLocaleString()}</p><pre className="whitespace-pre-wrap break-words text-sm">{row.body}</pre>{link ? <a className="underline" href={link} target="_blank" rel="noopener noreferrer">{t("website.openLink")}</a> : null}</li>
    })}</ul>
  </section>
}
