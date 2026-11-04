import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { SalesDocument } from "@/api/types"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { toastError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"
import { useUpdateDocument } from "../hooks"
import type { DocumentFamily } from "../model"
import { LinesEditor } from "./LinesEditor"
import { draftLines, lineInputs, validLines } from "../line-draft"

export function EditDocumentDialog({ document: d, family, onOpenChange, onReload }: {
  document: SalesDocument; family: DocumentFamily; onOpenChange: (open: boolean) => void; onReload: () => Promise<SalesDocument | undefined>
}) {
  const { t } = useTranslation()
  const update = useUpdateDocument(family, d.id)
  const [lines, setLines] = useState(() => draftLines(d.lines ?? [], d.currency))
  const [version, setVersion] = useState(d.version)
  const [conflict, setConflict] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const editable = d.status === "draft"
  return <>
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader><DialogTitle>{t("sales.editLines")}</DialogTitle><DialogDescription>{t("sales.editHint")}</DialogDescription></DialogHeader>
        <form id="sales-edit" onSubmit={(e) => {
          e.preventDefault()
          if (!editable || !validLines(lines, d.currency) || reloading) return
          update.mutate({ version, lines: lineInputs(lines, d.currency) }, {
            onSuccess: () => { toast.success(t("common.saved")); onOpenChange(false) },
            onError: (err) => { if (err instanceof ApiProblem && err.status === 422) setErrors(err.fieldErrors()); toastError(err, () => setConflict(true)) },
          })
        }}>
          <fieldset disabled={!editable || update.isPending || reloading}><LinesEditor lines={lines} currency={d.currency} onChange={(next) => { setLines(next); setErrors({}) }} errors={errors} /></fieldset>
          {!editable ? <p role="alert">{t("sales.locked")}</p> : null}
        </form>
        <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button type="submit" form="sales-edit" disabled={!editable || !validLines(lines, d.currency) || update.isPending || reloading}>{t("common.save")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => {
      setReloading(true)
      void onReload().then((fresh) => { if (fresh) { setVersion(fresh.version); setConflict(false) } }).finally(() => setReloading(false))
    }} />
  </>
}
