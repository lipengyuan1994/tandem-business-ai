import { Link, useParams } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { EmptyState } from "@/components/common/EmptyState"
import { MoneyText } from "@/components/common/MoneyText"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { toastError } from "@/lib/errors"
import { BillDialog } from "./BillDialog"
import { BillPaymentDialog } from "./BillPaymentDialog"
import { SupplierCreditDialog } from "./SupplierCreditDialog"
import { useBill, useVoidBill, useVoidBillPayment, useVoidSupplierCredit } from "./billHooks"
import { BILL_PAY, BILL_READ, BILL_VOID, BILL_WRITE, canCreditBill, canEditBill, type Bill, type BillPayment, type SupplierCredit } from "./billModel"
import { formatQuantity } from "@/lib/quantity"

export function BillPage() {
  const { id } = useParams({ from: "/app/purchasing/bills/$id" })
  return <BillDetail key={id} id={id} />
}

function BillDetail({ id }: { id: string }) {
  const { t } = useTranslation()
  const query = useBill(id)
  const voidBill = useVoidBill(id)
  const [dialog, setDialog] = useState<"edit" | "pay" | "credit" | "void" | null>(null)
  const [conflict, setConflict] = useState(false)
  const reload = async () => { const r = await query.refetch(); if (r.error) { toastError(r.error); return undefined } return r.data }
  const b = query.data
  const money = (amount: number) => b ? <MoneyText value={{ amount_minor: amount, currency: b.currency }} /> : null
  return <PermissionGate permission={BILL_READ} fallback={<EmptyState title={t("bills.noAccess")} />}>
    {query.isPending ? <Skeleton className="h-96" /> : query.error && !b ? <QueryError error={query.error} onRetry={() => reload()} notFound={t("bills.notFound")} /> : b ? <>
      {query.error ? <QueryError error={query.error} onRetry={() => reload()} /> : null}
      <div className="mb-4"><Link to="/purchasing" search={{ tab: "bills" }} className="record-link">{t("bills.back")}</Link></div>
      <PageHeader title={b.supplier_reference ? t("bills.titleWithReference", { reference: b.supplier_reference }) : t("bills.title")} description={b.supplier_name} actions={<div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={b.status} />
        {canEditBill(b) ? <PermissionGate permission={BILL_WRITE}><Button variant="outline" onClick={() => setDialog("edit")}>{t("bills.edit")}</Button></PermissionGate> : null}
        {b.status === "open" || b.status === "partially_paid" ? <PermissionGate permission={BILL_PAY}><Button onClick={() => setDialog("pay")}>{t("bills.payments.record")}</Button></PermissionGate> : null}
        {canCreditBill(b) ? <PermissionGate permission={BILL_WRITE}><Button variant="outline" onClick={() => setDialog("credit")}>{t("bills.credits.record")}</Button></PermissionGate> : null}
        {b.status === "open" && !b.amount_credited_minor ? <PermissionGate permission={BILL_VOID}><Button variant="outline" onClick={() => setDialog("void")}>{t("bills.void")}</Button></PermissionGate> : null}
      </div>} />
      <dl className="mb-6 grid gap-x-6 gap-y-2 sm:grid-cols-[max-content_1fr]">
        <dt className="text-muted-foreground">{t("bills.billDate")}</dt><dd>{b.bill_date}</dd>
        <dt className="text-muted-foreground">{t("bills.dueDate")}</dt><dd>{b.due_date ?? t("bills.noDueDate")}</dd>
        <dt className="text-muted-foreground">{t("bills.purchaseOrder")}</dt>
        <dd>{b.purchase_order_id ? <Link className="record-link" to="/purchasing/purchase-orders/$id" params={{ id: b.purchase_order_id }}>{b.purchase_order_number ?? t("status.draft")}</Link> : t("bills.noPurchaseOrder")}</dd>
        <dt className="text-muted-foreground">{t("bills.totalShort")}</dt><dd>{money(b.total_minor)}</dd>
        <dt className="text-muted-foreground">{t("bills.paid")}</dt><dd>{money(b.amount_paid_minor)}</dd>
        {b.amount_credited_minor ? <><dt className="text-muted-foreground">{t("bills.credited")}</dt><dd>{money(b.amount_credited_minor)}</dd></> : null}
        {b.amount_refunded_minor ? <><dt className="text-muted-foreground">{t("bills.refunded")}</dt><dd>{money(b.amount_refunded_minor)}</dd></> : null}
        <dt className="text-muted-foreground">{t("bills.balance")}</dt><dd className="font-semibold">{money(b.balance_minor)}</dd>
      </dl>
      {b.notes ? <p className="mb-6 whitespace-pre-wrap">{b.notes}</p> : null}
      {b.lines?.length ? <BillLines bill={b} /> : null}
      <BillPayments bill={b} onConflict={() => setConflict(true)} />
      {b.credits?.length ? <BillCredits bill={b} onConflict={() => setConflict(true)} /> : null}
      {dialog === "edit" ? <PermissionGate permission={BILL_WRITE}><BillDialog bill={b} onClose={() => setDialog(null)} onConflict={() => setConflict(true)} /></PermissionGate> : null}
      {dialog === "credit" ? <PermissionGate permission={BILL_WRITE}><SupplierCreditDialog bill={b} onClose={() => setDialog(null)} onConflict={() => setConflict(true)} /></PermissionGate> : null}
      {dialog === "pay" ? <PermissionGate permission={BILL_PAY}><BillPaymentDialog bill={b} onClose={() => setDialog(null)} onConflict={() => setConflict(true)} /></PermissionGate> : null}
      <ConfirmDialog open={dialog === "void"} onOpenChange={(open) => { if (!open) setDialog(null) }} destructive pending={voidBill.isPending}
        title={t("bills.confirmVoid.title")} description={t("bills.confirmVoid.text")} confirmLabel={t("bills.void")}
        onConfirm={() => voidBill.mutate({ version: b.version }, {
          onSuccess: () => { setDialog(null); toast.success(t("bills.voidDone")) },
          onError: (err) => { setDialog(null); toastError(err, () => setConflict(true)) },
        })} />
    </> : null}
    <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { void reload().then((fresh) => { if (fresh) setConflict(false) }) }} />
  </PermissionGate>
}

/** Payment history of a bill. Admins can void a payment entered in error, which reopens the balance. */
function BillPayments({ bill, onConflict }: { bill: Bill; onConflict: () => void }) {
  const { t } = useTranslation()
  const [voiding, setVoiding] = useState<BillPayment | null>(null)
  const mutation = useVoidBillPayment(bill.id)
  const payments = bill.payments ?? []
  return <section className="space-y-2" aria-label={t("bills.payments.title")}>
    <h2 className="text-lg font-semibold">{t("bills.payments.title")}</h2>
    {!payments.length ? <p>{t("bills.payments.none")}</p> : <Table>
      <caption className="sr-only">{t("bills.payments.title")}</caption>
      <TableHeader><TableRow>
        <TableHead>{t("bills.payments.date")}</TableHead><TableHead>{t("bills.payments.method")}</TableHead>
        <TableHead>{t("bills.payments.referenceShort")}</TableHead><TableHead>{t("bills.payments.amountShort")}</TableHead>
        <TableHead><span className="sr-only">{t("common.actions")}</span></TableHead>
      </TableRow></TableHeader>
      <TableBody>{payments.map((p) => <TableRow key={p.id} className={p.voided_at ? "text-muted-foreground line-through" : undefined}>
        <TableCell>{p.paid_on}</TableCell><TableCell>{t(`sales.payments.methods.${p.method}`)}</TableCell>
        <TableCell className="whitespace-normal">{p.reference ?? "—"}</TableCell>
        <TableCell><MoneyText value={{ amount_minor: p.amount_minor, currency: bill.currency }} />{p.voided_at ? <span className="ml-2 no-underline">{t("bills.payments.voided")}</span> : null}</TableCell>
        <TableCell>{!p.voided_at && bill.status !== "void" ? <PermissionGate permission={BILL_VOID}>
          <Button variant="outline" size="sm" disabled={mutation.isPending} onClick={() => setVoiding(p)}>{t("bills.payments.void")}</Button>
        </PermissionGate> : null}</TableCell>
      </TableRow>)}</TableBody>
    </Table>}
    <ConfirmDialog open={voiding !== null} onOpenChange={(open) => { if (!open) setVoiding(null) }} destructive pending={mutation.isPending}
      title={t("bills.payments.confirmVoid.title")} description={t("bills.payments.confirmVoid.text")} confirmLabel={t("bills.payments.void")}
      onConfirm={() => {
        if (!voiding) return
        mutation.mutateAsync({ paymentId: voiding.id, version: bill.version }).then(
          () => { setVoiding(null); toast.success(t("bills.payments.voidDone")) },
          (err: unknown) => { setVoiding(null); toastError(err, onConflict) },
        )
      }} />
  </section>
}

function BillLines({ bill }: { bill: Bill }) {
  const { t } = useTranslation()
  return <section className="mb-6 space-y-2" aria-label={t("bills.lines.title")}>
    <h2 className="text-lg font-semibold">{t("bills.lines.title")}</h2>
    <Table>
      <caption className="sr-only">{t("bills.lines.title")}</caption>
      <TableHeader><TableRow>
        <TableHead>{t("bills.lines.descriptionShort")}</TableHead><TableHead>{t("bills.lines.quantityShort")}</TableHead>
        <TableHead>{t("bills.lines.unitCostShort")}</TableHead><TableHead>{t("bills.payments.amountShort")}</TableHead>
      </TableRow></TableHeader>
      <TableBody>{(bill.lines ?? []).map((l) => <TableRow key={l.id}>
        <TableCell className="whitespace-normal">{l.description}</TableCell><TableCell>{formatQuantity(String(l.quantity))}</TableCell>
        <TableCell><MoneyText value={{ amount_minor: l.unit_cost_minor, currency: bill.currency }} /></TableCell>
        <TableCell><MoneyText value={{ amount_minor: l.amount_minor, currency: bill.currency }} /></TableCell>
      </TableRow>)}</TableBody>
    </Table>
  </section>
}

/** Credits the supplier gave against the bill, with any money they paid back. Admins can void one entered in error. */
function BillCredits({ bill, onConflict }: { bill: Bill; onConflict: () => void }) {
  const { t } = useTranslation()
  const [voiding, setVoiding] = useState<SupplierCredit | null>(null)
  const mutation = useVoidSupplierCredit(bill.id)
  const money = (minor: number) => <MoneyText value={{ amount_minor: minor, currency: bill.currency }} />
  return <section className="mt-6 space-y-2" aria-label={t("bills.credits.title")}>
    <h2 className="text-lg font-semibold">{t("bills.credits.title")}</h2>
    <Table>
      <caption className="sr-only">{t("bills.credits.title")}</caption>
      <TableHeader><TableRow>
        <TableHead>{t("bills.credits.date")}</TableHead><TableHead>{t("bills.payments.referenceShort")}</TableHead>
        <TableHead>{t("bills.payments.amountShort")}</TableHead><TableHead>{t("bills.credits.refundShort")}</TableHead>
        <TableHead><span className="sr-only">{t("common.actions")}</span></TableHead>
      </TableRow></TableHeader>
      <TableBody>{(bill.credits ?? []).map((c) => <TableRow key={c.id} className={c.voided_at ? "text-muted-foreground line-through" : undefined}>
        <TableCell>{c.credit_date}</TableCell>
        <TableCell className="whitespace-normal">{c.supplier_reference ?? "—"}{c.reason ? <span className="block text-sm text-muted-foreground">{c.reason}</span> : null}</TableCell>
        <TableCell>{money(c.amount_minor)}{c.voided_at ? <span className="ml-2 no-underline">{t("bills.payments.voided")}</span> : null}</TableCell>
        <TableCell>{c.refund_minor ? <>{money(c.refund_minor)} <span className="text-sm">{t("bills.credits.refundOn", {
          date: c.refund_received_on, method: t(`sales.payments.methods.${c.refund_method ?? "other"}`),
        })}{c.refund_reference ? `, ${c.refund_reference}` : ""}</span></> : "—"}</TableCell>
        <TableCell>{!c.voided_at && bill.status !== "void" ? <PermissionGate permission={BILL_VOID}>
          <Button variant="outline" size="sm" disabled={mutation.isPending} onClick={() => setVoiding(c)}>{t("bills.credits.void")}</Button>
        </PermissionGate> : null}</TableCell>
      </TableRow>)}</TableBody>
    </Table>
    <ConfirmDialog open={voiding !== null} onOpenChange={(open) => { if (!open) setVoiding(null) }} destructive pending={mutation.isPending}
      title={t("bills.credits.confirmVoid.title")} description={t("bills.credits.confirmVoid.text")} confirmLabel={t("bills.credits.void")}
      onConfirm={() => {
        if (!voiding) return
        mutation.mutateAsync({ creditId: voiding.id, version: bill.version }).then(
          () => { setVoiding(null); toast.success(t("bills.credits.voidDone")) },
          (err: unknown) => { setVoiding(null); toastError(err, onConflict) },
        )
      }} />
  </section>
}
