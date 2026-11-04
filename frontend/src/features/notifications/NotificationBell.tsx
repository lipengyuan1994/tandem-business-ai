import { QueryError } from "@/components/common/QueryError"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { Bell } from "lucide-react"
import { useTranslation } from "react-i18next"
import { api, unwrap } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"

const KEY = ["notifications", "mine"] as const

export function NotificationBell() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const list = useQuery({ queryKey: KEY, queryFn: async () => unwrap(await api.GET("/api/v1/notifications")), refetchInterval: 30_000 })
  const readOne = useMutation({
    mutationFn: async (id: string) => unwrap(await api.POST("/api/v1/notifications/{notification_id}/read", { params: { path: { notification_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  })
  const readAll = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/notifications/read-all")),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  })
  const unread = list.data?.unread ?? 0

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative">
          <Bell aria-hidden="true" />
          <span className="sr-only">{t("notifications.label", { count: unread })}</span>
          {unread > 0 ? <span className="absolute -right-0.5 -top-0.5 rounded-full bg-destructive px-1 text-[10px] text-white" aria-hidden="true">{unread}</span> : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <p className="text-sm font-medium">{t("notifications.title")}</p>
          {unread > 0 ? <Button size="sm" variant="ghost" onClick={() => readAll.mutate()}>{t("notifications.readAll")}</Button> : null}
        </div>
        {list.error ? <QueryError error={list.error} onRetry={() => list.refetch()} /> : list.isPending ? <p role="status" className="p-4">{t("common.loading")}</p> : <ul className="max-h-96 overflow-auto" aria-live="polite">
          {(list.data?.items ?? []).length === 0 ? <li className="p-4 text-sm text-muted-foreground">{t("notifications.empty")}</li> : null}
          {list.data?.items.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                className={`w-full px-3 py-2 text-left text-sm hover:bg-accent ${n.read_at ? "text-muted-foreground" : "font-medium"}`}
                onClick={() => { if (!n.read_at) readOne.mutate(n.id); if (n.link) void navigate({ to: n.link }) }}
              >
                {n.title}
              </button>
            </li>
          ))}
        </ul>}
      </PopoverContent>
    </Popover>
  )
}
