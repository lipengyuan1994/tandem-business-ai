import { Link, useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { Schemas } from "@/api/types"
import { useSession } from "@/app/session-context"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { useSuppliers } from "@/features/purchasing/hooks"
import { formatQuantity as qty } from "@/lib/quantity"
import { toastError } from "@/lib/errors"
import { useConvertDraft, useReorderDrafts } from "../hooks"

type Draft = Schemas["ReorderDraftOut"]
type Supplier = Schemas["SupplierOut"]

function DraftRow({ draft, names, suppliers, canConvert }: { draft: Draft; names: Map<string, string>; suppliers: Supplier[]; canConvert: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const convert = useConvertDraft()
  const [supplierId, setSupplierId] = useState("")
  const label = t("inventory.drafts.label", { count: draft.lines.length })
  return (
    <li className="space-y-2 py-2 first:pt-0 last:pb-0">
      <ul className="text-sm">
        {draft.lines.map((l) => <li key={l.item_id}><span className="font-medium">{names.get(l.item_id) ?? t("inventory.drafts.unknownItem")}</span>: {qty(l.quantity)}</li>)}
      </ul>
      {canConvert ? (
        suppliers.every((s) => !s.active) ? <p className="text-sm text-muted-foreground">{t("inventory.drafts.noSuppliers")} <Link className="underline" to="/purchasing" search={{ tab: "suppliers" }}>{t("inventory.drafts.addSupplier")}</Link></p> : (
          <form className="flex flex-wrap items-end gap-2" aria-label={label} onSubmit={(e) => {
            e.preventDefault()
            if (!supplierId) return
            convert.mutate({ draft_id: draft.id, supplier_id: supplierId, version: draft.version }, {
              onSuccess: (po) => { toast.success(t("inventory.drafts.converted")); void navigate({ to: "/purchasing/purchase-orders/$id", params: { id: po.id } }) },
              onError: (err) => toastError(err),
            })
          }}>
            <div className="min-w-0 flex-1 space-y-1">
              <Label htmlFor={`draft-supplier-${draft.id}`}>{t("inventory.drafts.supplier")}</Label>
              <select id={`draft-supplier-${draft.id}`} className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
                <option value="">{t("inventory.drafts.chooseSupplier")}</option>
                {suppliers.filter((s) => s.active).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
            <Button type="submit" size="sm" disabled={!supplierId || convert.isPending}>{t("inventory.drafts.convert")}</Button>
          </form>
        )
      ) : null}
    </li>
  )
}

/** Open reorder drafts. Each becomes a purchase order once a supplier is chosen; nothing is ordered until the PO is sent. */
export function DraftsPanel({ names }: { names: Map<string, string> }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const drafts = useReorderDrafts(permissions.has("inventory.reorder.read"))
  const canConvert = permissions.has("purchasing.po.write") && permissions.has("purchasing.po.read")
  const suppliers = useSuppliers()
  if (drafts.error) return <QueryError error={drafts.error} onRetry={() => drafts.refetch()} />
  const open = (drafts.data ?? []).filter((d) => d.status === "draft")
  if (open.length === 0) return null
  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">{t("inventory.drafts.title")}</CardTitle></CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm text-muted-foreground">{t("inventory.drafts.hint")}</p>
        {suppliers.error && canConvert ? <QueryError error={suppliers.error} onRetry={() => suppliers.refetch()} /> : null}
        <ul className="divide-y" aria-label={t("inventory.drafts.title")}>
          {open.map((d) => <DraftRow key={d.id} draft={d} names={names} suppliers={suppliers.data ?? []} canConvert={canConvert && !!suppliers.data} />)}
        </ul>
      </CardContent>
    </Card>
  )
}
