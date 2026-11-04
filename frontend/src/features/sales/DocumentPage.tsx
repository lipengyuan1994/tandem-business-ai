import { QueryError } from "@/components/common/QueryError"
import { Link, useParams } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { EmptyState } from "@/components/common/EmptyState"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toastError } from "@/lib/errors"
import { DocumentActions } from "./components/DocumentActions"
import { DocumentBody } from "./components/DocumentBody"
import { EditDocumentDialog } from "./components/EditDocumentDialog"
import { useSalesDocument } from "./hooks"
import { READ, WRITE, type DocumentFamily } from "./model"
import "./print.css"

export function QuotePage() { const { id } = useParams({ from: "/app/sales/quotes/$id" }); return <DocumentPage key={id} family="quotes" id={id} /> }
export function OrderPage() { const { id } = useParams({ from: "/app/sales/orders/$id" }); return <DocumentPage key={id} family="orders" id={id} /> }
export function InvoicePage() { const { id } = useParams({ from: "/app/sales/invoices/$id" }); return <DocumentPage key={id} family="invoices" id={id} /> }
function DocumentPage({ family, id }: { family: DocumentFamily; id: string }) {
  const { t } = useTranslation()
  const query = useSalesDocument(family, id)
  const [editing, setEditing] = useState(false)
  const [conflict, setConflict] = useState(false)
  const reload = async () => {
    const result = await query.refetch()
    if (result.error) { toastError(result.error); return undefined }
    return result.data
  }
  const d = query.data
  return <PermissionGate permission={READ} fallback={<EmptyState title={t("sales.noAccess")} />}>
    {query.isPending ? <Skeleton className="h-96 w-full" /> : query.error ? <QueryError error={query.error} onRetry={() => query.refetch()} notFound={t("sales.notFound")} /> : d ? <>
      <div className="mb-4 print:hidden"><Link to="/sales" className="record-link">{t("sales.back")}</Link></div>
      <PageHeader title={d.number ?? t("status.draft")} description={d.bill_to_name} actions={<div className="flex items-center gap-2 print:hidden"><StatusBadge status={d.status} />{d.status === "draft" ? <PermissionGate permission={WRITE}><Button onClick={() => setEditing(true)}>{t("sales.editLines")}</Button></PermissionGate> : null}<DocumentActions family={family} document={d} onConflict={() => setConflict(true)} /><Button variant="outline" onClick={() => window.print()}>{t("sales.print")}</Button></div>} />
      {d.status !== "draft" ? <p className="mb-6 text-sm text-muted-foreground print:hidden">{t(family === "invoices" ? "sales.invoiceLocked" : "sales.locked")}</p> : null}
      <DocumentBody family={family} document={d} />
      {editing ? <PermissionGate permission={WRITE}><EditDocumentDialog family={family} document={d} onOpenChange={setEditing} onReload={reload} /></PermissionGate> : null}
    </> : <EmptyState title={t("sales.notFound")} />}
    <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { void reload().then((fresh) => { if (fresh) setConflict(false) }) }} />
  </PermissionGate>
}
