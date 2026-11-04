import { QueryError } from "@/components/common/QueryError"
import { Table } from "@/components/ui/table"
import { formatQuantity } from "@/lib/quantity"
import { Link, useParams } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { useSession } from "@/app/session-context"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { MoneyText } from "@/components/common/MoneyText"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toastError } from "@/lib/errors"
import { OrderDialog } from "./OrderDialog"
import { ReceiveDialog } from "./ReceiveDialog"
import { useOrder, useOrderAction } from "./hooks"
import { APPROVE, READ, RECEIVE, WRITE } from "./model"

export function OrderPage() {
  const { id } = useParams({ from: "/app/purchasing/purchase-orders/$id" })
  return <OrderDetail key={id} id={id} />
}
function OrderDetail({ id }: { id: string }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const query = useOrder(id)
  const action = useOrderAction(id)
  const [confirm, setConfirm] = useState<"approve" | "send" | "cancel" | null>(null)
  const [conflict, setConflict] = useState(false)
  const [editing, setEditing] = useState(false)
  const [receiving, setReceiving] = useState(false)
  const reload = async () => { const r = await query.refetch(); if (r.error) { toastError(r.error); return undefined } return r.data }
  const d = query.data
  return <PermissionGate permission={READ} fallback={<EmptyState title={t("purchasing.noAccess")} />}>
    {query.isPending ? <Skeleton className="h-96" /> : query.error && !d ? <QueryError error={query.error} onRetry={() => reload()} notFound={t("purchasing.notFound")} /> : d ? <>
      {query.error ? <QueryError error={query.error} onRetry={() => reload()} /> : null}
      <div className="mb-4"><Link to="/purchasing" className="record-link">{t("purchasing.back")}</Link></div>
      <PageHeader title={d.number ?? t("status.draft")} description={d.supplier_name} actions={<div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={d.status} />
        {d.status === "draft" ? <><PermissionGate permission={WRITE}><Button onClick={() => setEditing(true)}>{t("purchasing.editPO")}</Button></PermissionGate><PermissionGate permission={APPROVE}><Button onClick={() => setConfirm("approve")}>{t("purchasing.action.approve")}</Button></PermissionGate></> : null}
        {d.status === "approved" ? <PermissionGate permission={WRITE}><Button onClick={() => setConfirm("send")}>{t("purchasing.action.send")}</Button></PermissionGate> : null}
        {["sent", "partially_received"].includes(d.status) ? <PermissionGate permission={RECEIVE}><Button onClick={() => setReceiving(true)}>{t("purchasing.receive")}</Button></PermissionGate> : null}
        {!["received", "cancelled"].includes(d.status) ? <PermissionGate permission={WRITE}><Button variant="outline" onClick={() => setConfirm("cancel")}>{t("purchasing.action.cancel")}</Button></PermissionGate> : null}
      </div>} />
      <div className="rounded-lg border"><Table className="text-left"><caption className="sr-only">{t("purchasing.lines")}</caption><thead><tr>{["item", "quantity", "unitCost", "received", "total"].map((c) => <th scope="col" key={c} className="border-b p-2">{t(`purchasing.${c}`)}</th>)}</tr></thead>
        <tbody>{(d.lines ?? []).map((line) => <tr key={line.id}><td className="p-2">{line.name}</td><td className="p-2">{formatQuantity(line.quantity)}</td><td className="p-2"><MoneyText value={{ amount_minor: line.unit_cost_minor, currency: d.currency }} /></td><td className="p-2">{formatQuantity(line.received_quantity)}</td><td className="p-2"><MoneyText value={{ amount_minor: line.line_total_minor, currency: d.currency }} /></td></tr>)}</tbody>
      </Table></div>
      <dl className="my-4 flex gap-4"><dt>{t("purchasing.total")}</dt><dd><MoneyText value={{ amount_minor: d.total_minor, currency: d.currency }} /></dd></dl>
      {d.notes ? <p className="mb-6 whitespace-pre-wrap">{d.notes}</p> : null}
      <section className="space-y-3" aria-label={t("purchasing.receiptHistory")}><h2 className="text-lg font-semibold">{t("purchasing.receiptHistory")}</h2>
        {!(d.receipts ?? []).length ? <p>{t("purchasing.noReceipts")}</p> : (d.receipts ?? []).map((r) => <div className="rounded-md border p-3" key={r.id}>
          <LocalTime iso={r.created_at} timeZone={businessTimeZone} />
          <ul>{(r.lines ?? []).map((line) => <li key={line.po_line_id}>{(d.lines ?? []).find((l) => l.id === line.po_line_id)?.name}: {formatQuantity(line.quantity)}</li>)}</ul>
          {r.override_reason ? <p>{t("purchasing.overrideNote", { reason: r.override_reason })}</p> : null}
        </div>)}
      </section>
      {confirm ? <ConfirmDialog open onOpenChange={(open) => { if (!open) setConfirm(null) }} title={t(`purchasing.confirm.${confirm}`)} description={t(`purchasing.confirmHint.${confirm}`)} confirmLabel={t(`purchasing.action.${confirm}`)} pending={action.isPending} destructive={confirm === "cancel"} onConfirm={() => action.mutate({ action: confirm, version: d.version }, { onSuccess: () => { toast.success(t("common.saved")); setConfirm(null) }, onError: (e) => { setConfirm(null); toastError(e, () => setConflict(true)) } })} /> : null}
      {editing ? <PermissionGate permission={WRITE}><OrderDialog order={d} onClose={() => setEditing(false)} reload={reload} /></PermissionGate> : null}
      {receiving ? <PermissionGate permission={RECEIVE}><ReceiveDialog order={d} onClose={() => setReceiving(false)} reload={reload} /></PermissionGate> : null}
    </> : null}
    <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { void reload().then((fresh) => { if (fresh) setConflict(false) }) }} />
  </PermissionGate>
}
