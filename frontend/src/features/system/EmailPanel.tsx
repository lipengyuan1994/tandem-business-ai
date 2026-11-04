import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { api, unwrap } from "@/api/client"
import type { SystemStatus } from "@/api/types"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toastError } from "@/lib/errors"

export function EmailPanel({ email }: { email: SystemStatus["email"] }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [to, setTo] = useState("")
  const send = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/admin/test-email", { body: { to } })),
    onSuccess: () => { setOpen(false); setTo(""); toast.success(t("system.email.queued")) },
    onError: toastError,
  })
  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">{t("system.email.title")}</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>{email.configured ? t("system.email.configured", { host: email.host }) : t("system.email.disabled")}</p>
        <PermissionGate permission="admin_ops.system.read">
          <Button disabled={!email.configured} onClick={() => setOpen(true)}>{t("system.email.test")}</Button>
        </PermissionGate>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("system.email.test")}</DialogTitle>
              <DialogDescription>{t("system.email.hint")}</DialogDescription>
            </DialogHeader>
            <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); send.mutate() }}>
              <Label htmlFor="test-email-to">{t("system.email.recipient")}</Label>
              <Input id="test-email-to" type="email" required maxLength={254} value={to} onChange={(event) => setTo(event.target.value)} />
              <Button type="submit" disabled={send.isPending || !to}>{t("system.email.send")}</Button>
            </form>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  )
}
