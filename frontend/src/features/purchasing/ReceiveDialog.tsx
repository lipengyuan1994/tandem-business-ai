import { formatQuantity } from "@/lib/quantity"
import { useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { Schemas } from "@/api/types"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { describeError, toastError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"
import { FieldError } from "./FieldError"
import { useReceive } from "./hooks"
import { OVERRIDE, type PO } from "./model"
import { fieldInvalid } from "./validation"

type Recovery = "ready" | "unknown" | "conflict" | "review"
interface ReceiptAttempt { idempotencyKey: string; body: Schemas["ReceiptIn"] }

export function ReceiveDialog({ order, onClose, reload }: { order: PO; onClose: () => void; reload: () => Promise<PO | undefined> }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const save = useReceive(order.id)
  const attempt = useRef<ReceiptAttempt | null>(null)
  const inFlight = useRef(false)
  const [version, setVersion] = useState(order.version)
  const [quantities, setQuantities] = useState<Record<string, string>>({})
  const [override, setOverride] = useState(false)
  const [reason, setReason] = useState("")
  const [recovery, setRecovery] = useState<Recovery>("ready")
  const [review, setReview] = useState<PO | null>(null)
  const [reloading, setReloading] = useState(false)
  const [reloadFailed, setReloadFailed] = useState(false)
  const locked = recovery !== "ready" || save.isPending || reloading
  const unresolved = recovery === "unknown" || recovery === "conflict"
  const selected = (order.lines ?? []).filter((line) => quantities[line.id] && quantities[line.id] !== "0")
  const valid = selected.length > 0 && selected.every((line) => /^\d+(?:\.\d{1,3})?$/.test(quantities[line.id] ?? "") && Number(quantities[line.id]) > 0) && (!override || reason.trim())

  function send(original: ReceiptAttempt) {
    if (inFlight.current || reloading) return
    inFlight.current = true
    save.mutate(original, {
      onSuccess: () => { inFlight.current = false; toast.success(t("purchasing.receivedToast")); onClose() },
      onError: (error) => {
        inFlight.current = false
        if (error instanceof ApiProblem && error.isConflict) {
          setRecovery("conflict")
        } else if (error instanceof ApiProblem && error.status >= 400 && error.status < 500 && error.status !== 408 && recovery === "ready") {
          // A definite rejection permits correction; an unknown result keeps the frozen attempt.
          attempt.current = null
          setRecovery("ready")
        } else {
          setRecovery("unknown")
        }
        toastError(error)
      },
    })
  }

  async function reloadDeliveries() {
    if (inFlight.current || reloading) return
    setReloading(true)
    setReloadFailed(false)
    try {
      const fresh = await reload()
      if (!fresh) { setReloadFailed(true); return }
      setReview(fresh)
      setRecovery("review")
    } catch (error) {
      toastError(error)
      setReloadFailed(true)
    } finally {
      setReloading(false)
    }
  }

  function startAnother() {
    if (!review || recovery !== "review") return
    // A new delivery starts empty only after the user has reviewed the actual saved receipts.
    setVersion(review.version)
    setQuantities({})
    setOverride(false)
    setReason("")
    attempt.current = null
    setReview(null)
    setRecovery("ready")
    setReloadFailed(false)
    save.reset()
  }

  return <Dialog open onOpenChange={(open) => { if (!open && !unresolved && !save.isPending && !reloading) onClose() }}><DialogContent showCloseButton={!unresolved && !save.isPending && !reloading} className="max-h-[90dvh] overflow-y-auto">
    <DialogHeader><DialogTitle>{t("purchasing.receive")}</DialogTitle><DialogDescription>{t("purchasing.receiveHint")}</DialogDescription></DialogHeader>
    <form id="receipt-form" className="space-y-4" onSubmit={(e) => {
      e.preventDefault()
      if (!valid || locked || inFlight.current) return
      const original = { idempotencyKey: crypto.randomUUID(), body: { version, lines: selected.map((line) => ({ po_line_id: line.id, quantity: quantities[line.id]! })), override, reason: override ? reason.trim() : null } }
      attempt.current = original
      send(original)
    }}>
      {(order.lines ?? []).map((line) => <div key={line.id} className="space-y-1"><Label htmlFor={`receipt-${line.id}`}>{t("purchasing.receiveItem", { item: line.name })}</Label>
        <p className="text-sm text-muted-foreground">{t("purchasing.progress", { received: formatQuantity(line.received_quantity), ordered: formatQuantity(line.quantity) })}</p>
        <Input id={`receipt-${line.id}`} disabled={locked} aria-invalid={fieldInvalid(save.error, `lines.${selected.findIndex((l) => l.id === line.id)}.quantity`)} inputMode="decimal" value={quantities[line.id] ?? ""} onChange={(e) => setQuantities({ ...quantities, [line.id]: e.target.value })} /><FieldError error={save.error} field={`lines.${selected.findIndex((l) => l.id === line.id)}.quantity`} />
      </div>)}
      {permissions.has(OVERRIDE) ? <div className="space-y-2"><label className="flex items-center gap-2"><input type="checkbox" disabled={locked} checked={override} onChange={(e) => setOverride(e.target.checked)} />{t("purchasing.override")}</label>
        {override ? <div><Label htmlFor="receipt-reason">{t("purchasing.reason")}</Label><Textarea dictation={{ context: "purchasing" }} id="receipt-reason" disabled={locked} aria-invalid={fieldInvalid(save.error, "reason")} required value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} /><FieldError error={save.error} field="reason" /></div> : null}
      </div> : null}
      {save.error ? <p role="alert" className="text-sm text-destructive">{describeError(save.error)}</p> : null}
    </form>
    {unresolved ? <div className="space-y-3" aria-live="polite">
      <p>{t(recovery === "unknown" ? "purchasing.receiptUnknown" : "purchasing.receiptConflict")}</p>
      <div className="flex flex-wrap gap-2">
        {recovery === "unknown" ? <Button variant="outline" disabled={save.isPending || reloading} onClick={() => { if (attempt.current) send(attempt.current) }}>{t("purchasing.retryReceipt")}</Button> : null}
        <Button variant="outline" disabled={save.isPending || reloading} onClick={() => void reloadDeliveries()}>{t("purchasing.reloadDeliveries")}</Button>
      </div>
      {reloadFailed ? <p role="alert" className="text-sm text-destructive">{t("purchasing.receiptReloadFailed")}</p> : null}
    </div> : null}
    {recovery === "review" && review ? <section className="space-y-2 rounded-md border p-3" aria-label={t("purchasing.recordedDeliveries")}>
      <h2 className="font-semibold">{t("purchasing.recordedDeliveries")}</h2>
      {(review.lines ?? []).map((line) => <div key={line.id}><p>{line.name}</p><p>{t("purchasing.progress", { received: formatQuantity(line.received_quantity), ordered: formatQuantity(line.quantity) })}</p></div>)}
      {(review.receipts ?? []).length ? <ul>{review.receipts?.flatMap((receipt) => (receipt.lines ?? []).map((line) => <li key={`${receipt.id}-${line.po_line_id}`}>{review.lines?.find((l) => l.id === line.po_line_id)?.name}: {formatQuantity(line.quantity)}</li>))}</ul> : <p>{t("purchasing.noReceipts")}</p>}
      <p>{t("purchasing.reviewDeliveriesHint")}</p>
      {["sent", "partially_received"].includes(review.status) ? <Button onClick={startAnother}>{t("purchasing.startAnotherReceipt")}</Button> : null}
    </section> : null}
    <DialogFooter><Button variant="outline" disabled={unresolved || save.isPending || reloading} onClick={onClose}>{t("common.cancel")}</Button><Button form="receipt-form" disabled={!valid || locked}>{t("purchasing.recordReceipt")}</Button></DialogFooter>
  </DialogContent></Dialog>
}
