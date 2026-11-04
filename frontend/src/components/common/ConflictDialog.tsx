import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"

/** Shown on HTTP 409 version conflicts. Never auto-overwrite another person's change (09 data access pattern). */
export function ConflictDialog({ open, onOpenChange, onReload }: { open: boolean; onOpenChange: (open: boolean) => void; onReload: () => void }) {
  const { t } = useTranslation()
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("conflict.title")}</DialogTitle>
          <DialogDescription>{t("conflict.description")}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button onClick={onReload}>{t("conflict.reload")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
