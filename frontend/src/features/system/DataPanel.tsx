import { QueryError } from "@/components/common/QueryError"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { LocalTime } from "@/components/common/LocalTime"
import { PermissionGate } from "@/components/common/PermissionGate"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { toastError } from "@/lib/errors"
import { useErasures, useExportData } from "./data-hooks"

/** Owner-only: take all data out, and see which customers' personal data was erased (M18). */
export function DataPanel() {
  return <PermissionGate permission="admin_ops.data.manage"><Inner /></PermissionGate>
}

function Inner() {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const exportData = useExportData()
  const erasures = useErasures(true)
  return (
    <Card className="md:col-span-2">
      <CardHeader><CardTitle className="text-sm">{t("system.data.title")}</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>{t("system.data.exportHint")}</p>
        <Button disabled={exportData.isPending} onClick={() => exportData.mutate(undefined, { onSuccess: (name) => toast.success(t("system.data.downloaded", { name })), onError: (e) => toastError(e) })}>
          {t("system.data.export")}
        </Button>
        <section aria-labelledby="erasures-h">
          <h3 id="erasures-h" className="mb-1 font-medium">{t("system.data.erasures")}</h3>
          {erasures.error ? <QueryError error={erasures.error} onRetry={() => erasures.refetch()} /> : erasures.isPending ? <p role="status">{t("common.loading")}</p> : erasures.data?.length ? (
            <ul className="space-y-1">
              {erasures.data.map((e) => (
                <li key={e.id}>{t("system.data.erasureLine", { id: e.customer_id.slice(0, 8) })} · {e.status} · {e.completed_at ? <LocalTime iso={e.completed_at} timeZone={businessTimeZone} pattern="d MMM yyyy HH:mm" /> : null}</li>
              ))}
            </ul>
          ) : <p className="text-muted-foreground">{t("system.data.noErasures")}</p>}
          <p className="mt-1 text-xs text-muted-foreground">{t("system.data.erasureHow")}</p>
        </section>
      </CardContent>
    </Card>
  )
}
