import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toastError } from "@/lib/errors"
import { useSupplierLink } from "./hooks"

/**
 * Staff hand a supplier its own quote page themselves, for when no e-mail can (the demo has none). The link works like a
 * password for that supplier: it goes to the clipboard and nowhere else. Only if the browser refuses to copy is it shown,
 * in a dialog, so it can be copied by hand.
 */
export function CopyLinkButton({ requestId, supplierId, supplierName }: { requestId: string; supplierId: string; supplierName: string }) {
  const { t } = useTranslation()
  const link = useSupplierLink()
  const [manual, setManual] = useState<string | null>(null)
  const copy = () => link.mutate({ requestId, supplierId }, {
    onSuccess: async ({ url }) => {
      try { await navigator.clipboard.writeText(url); toast.success(t("procurement.linkCopied", { name: supplierName })) } catch { setManual(url) }
    },
    onError: (error) => toastError(error),
  })
  return <>
    <Button variant="outline" disabled={link.isPending} aria-label={t("procurement.copyLinkFor", { name: supplierName })} onClick={copy}>{t("procurement.copyLink")}</Button>
    {manual ? <Dialog open onOpenChange={(open) => { if (!open) setManual(null) }}><DialogContent>
      <DialogHeader><DialogTitle>{t("procurement.linkTitle", { name: supplierName })}</DialogTitle><DialogDescription>{t("procurement.linkManual")}</DialogDescription></DialogHeader>
      <Label htmlFor="supplier-link">{t("procurement.linkField")}</Label>
      <Input id="supplier-link" readOnly value={manual} onFocus={(event) => event.currentTarget.select()} />
      <DialogFooter><Button variant="outline" onClick={() => setManual(null)}>{t("common.close")}</Button></DialogFooter>
    </DialogContent></Dialog> : null}
  </>
}
