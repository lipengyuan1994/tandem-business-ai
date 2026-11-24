import { Link } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { MoneyText } from "@/components/common/MoneyText"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { BillDialog } from "./BillDialog"
import { useBills, usePOBilling } from "./billHooks"
import { BILLABLE_PO_STATUSES, BILL_READ, BILL_WRITE } from "./billModel"
import type { PO } from "./model"

/** Bills recorded against a purchase order, with what was received but not billed yet. */
export function OrderBilling({ order }: { order: PO }) {
  return BILLABLE_PO_STATUSES.includes(order.status) ? <PermissionGate permission={BILL_READ}><Billing order={order} /></PermissionGate> : null
}

function Billing({ order }: { order: PO }) {
  const { t } = useTranslation()
  const [creating, setCreating] = useState(false)
  const billing = usePOBilling(order.id)
  const bills = useBills({ filter: "all", purchaseOrderId: order.id })
  const money = (amount: number) => <MoneyText value={{ amount_minor: amount, currency: order.currency }} />
  const rows = bills.data?.pages.flatMap((p) => p.items) ?? []
  return <section className="mt-6 space-y-3" aria-label={t("bills.forOrder")}>
    <div className="flex flex-wrap items-center gap-3">
      <h2 className="text-lg font-semibold">{t("bills.forOrder")}</h2>
      <PermissionGate permission={BILL_WRITE}><Button variant="outline" onClick={() => setCreating(true)}>{t("bills.new")}</Button></PermissionGate>
    </div>
    {billing.error ? <QueryError error={billing.error} onRetry={() => void billing.refetch()} /> : billing.data ? <dl className="flex flex-wrap gap-x-6 gap-y-1">
      <dt className="text-muted-foreground">{t("bills.received")}</dt><dd>{money(billing.data.received_minor)}</dd>
      <dt className="text-muted-foreground">{t("bills.billed")}</dt><dd>{money(billing.data.billed_minor)}</dd>
      <dt className="text-muted-foreground">{t("bills.unbilled")}</dt><dd>{money(billing.data.unbilled_received_minor)}</dd>
    </dl> : null}
    {bills.error ? <QueryError error={bills.error} onRetry={() => void bills.refetch()} /> : rows.length ? <ul className="space-y-1">{rows.map((b) => <li key={b.id} className="flex flex-wrap items-center gap-2">
      <Link className="record-link" to="/purchasing/bills/$id" params={{ id: b.id }}>{b.supplier_reference ?? t("bills.noReference")}</Link>
      <span>{b.bill_date}</span><MoneyText value={{ amount_minor: b.total_minor, currency: b.currency }} /><StatusBadge status={b.status} />
    </li>)}</ul> : bills.data ? <p>{t("bills.noneForOrder")}</p> : null}
    {creating ? <PermissionGate permission={BILL_WRITE}><BillDialog purchaseOrder={{ id: order.id, supplierId: order.supplier_id, number: order.number }} onClose={() => setCreating(false)} /></PermissionGate> : null}
  </section>
}
