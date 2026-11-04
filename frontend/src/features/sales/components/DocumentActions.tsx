import { Link, useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { SalesDocument } from "@/api/types"
import { useSession } from "@/app/session-context"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { Button } from "@/components/ui/button"
import { describeError, toastError } from "@/lib/errors"
import { useConversionTarget, useSalesAction } from "../hooks"
import { actionPermission, allowedActions, destination, DOCUMENT_ROUTES, type DocumentFamily, type SalesAction } from "../model"

export function DocumentActions({ document: d, family, onConflict }: { document: SalesDocument; family: DocumentFamily; onConflict: () => void }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const target = useConversionTarget(family, d)
  const actions = allowedActions(family, d).filter((a) => permissions.has(actionPermission(a)))
  const converts = actions.some((a) => a === "convert" || a === "invoice")
  return <div className="flex flex-wrap items-center gap-2 print:hidden">
    {actions.map((action) => (action !== "convert" && action !== "invoice") || (target.isSuccess && !target.data) ?
      <ActionButton key={action} action={action} document={d} family={family} onConflict={onConflict} /> : null)}
    {converts && target.isPending ? <p role="status" className="text-sm">{t("sales.checkingConversion")}</p> : null}
    {converts && target.error ? <div role="alert"><p>{describeError(target.error)}</p><Button variant="outline" onClick={() => void target.refetch()}>{t("common.retry")}</Button></div> : null}
    {converts && target.data ? <Link className="text-sm underline" to={DOCUMENT_ROUTES[family === "quotes" ? "orders" : "invoices"]} params={{ id: target.data.id }}>{t("sales.openConverted")}</Link> : null}
  </div>
}
function ActionButton({ action, document: d, family, onConflict }: { action: SalesAction; document: SalesDocument; family: DocumentFamily; onConflict: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const mutation = useSalesAction(family, d.id, action)
  const [confirmVersion, setConfirmVersion] = useState<number | null>(null)
  const irreversible = ["issue", "credit", "cancel"].includes(action)
  // mutateAsync, not mutate(…, {onSuccess}): a successful conversion refreshes the page data, which can unmount this
  // button before per-call callbacks run, and then the navigation to the new document would be skipped.
  const run = (version: number) => mutation.mutateAsync(version).then(
    (result) => {
      setConfirmVersion(null)
      toast.success(t("sales.actionDone"))
      if (["convert", "invoice", "credit"].includes(action)) void navigate({ to: DOCUMENT_ROUTES[destination(family, action)], params: { id: result.id } })
    },
    (err: unknown) => { setConfirmVersion(null); toastError(err, onConflict) },
  )
  return <>
    <Button variant={action === "credit" || action === "cancel" ? "destructive" : "outline"} disabled={mutation.isPending}
      onClick={() => irreversible ? setConfirmVersion(d.version) : run(d.version)}>{t(`sales.actions.${action}`)}</Button>
    {irreversible ? <ConfirmDialog open={confirmVersion !== null} onOpenChange={(open) => { if (!open) setConfirmVersion(null) }}
      title={t(`sales.confirm.${action}.title`)} description={t(`sales.confirm.${action}.text`)} confirmLabel={t(`sales.actions.${action}`)}
      destructive={action !== "issue"} pending={mutation.isPending} onConfirm={() => { if (confirmVersion !== null) run(confirmVersion) }} /> : null}
  </>
}
