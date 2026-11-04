import { QueryError } from "@/components/common/QueryError"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { CustomerDetail } from "@/api/types"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useSystemStatus } from "@/features/system/hooks"
import { useEraseCustomer } from "@/features/system/data-hooks"
import { describeError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"

/** Typed confirmation: erasure in standard, retention hold in regulated. */
export function EraseDialog({ customer, onOpenChange }: { customer: CustomerDetail; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const erase = useEraseCustomer()
  const profile = useSystemStatus()
  const wording = profile.data?.data_profile === "regulated" ? "crm.restrict" : "crm.erase"
  const [typed, setTyped] = useState("")
  const [reasons, setReasons] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    if (!profile.data || typed.trim() !== customer.display_name || erase.isPending) return
    erase.mutate({ customer_id: customer.id, confirm_name: typed }, {
      onSuccess: () => { toast.success(t(`${wording}.done`)); onOpenChange(false); void navigate({ to: "/customers" }) },
      onError: (e) => {
        if (e instanceof ApiProblem && e.code === "admin_ops.erasure_blocked") { setReasons((e.problem.reasons as string[] | undefined) ?? []); setError(null) }
        else { setReasons([]); setError(describeError(e)) }
      },
    })
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{profile.data ? t(`${wording}.title`, { name: customer.display_name }) : t("common.loading")}</DialogTitle>
          <DialogDescription>{profile.data ? t(`${wording}.description`) : t("common.loading")}</DialogDescription>
        </DialogHeader>
        {profile.data ? <ul className="list-disc space-y-1 pl-5 text-sm">
          {(wording === "crm.restrict" ? ["checks", "removes", "keeps", "backups"] : ["removes", "keeps", "backups"]).map((k) => <li key={k}>{t(`${wording}.${k}`)}</li>)}
        </ul> : null}
        {profile.error ? <QueryError error={profile.error} onRetry={() => profile.refetch()} /> : null}
        {reasons.length ? (
          <Alert variant="destructive" role="alert">
            <AlertTitle>{t("crm.erase.blocked")}</AlertTitle>
            <AlertDescription><ul className="list-disc pl-5">{reasons.map((r) => <li key={r}>{r}</li>)}</ul></AlertDescription>
          </Alert>
        ) : null}
        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
        <form id="erase-form" onSubmit={(e) => { e.preventDefault(); submit() }} className="space-y-1">
          <Label htmlFor="erase-confirm">{t("crm.erase.confirmLabel", { name: customer.display_name })}</Label>
          <Input id="erase-confirm" value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} />
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button type="submit" form="erase-form" variant="destructive" disabled={!profile.data || typed.trim() !== customer.display_name || erase.isPending}>{t(`${wording}.confirm`)}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
