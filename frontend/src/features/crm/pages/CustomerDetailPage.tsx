import { QueryError } from "@/components/common/QueryError"
import { useParams } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { toastError } from "@/lib/errors"
import { ConsentPanel } from "../components/ConsentPanel"
import { CustomerDialog } from "../components/CustomerDialog"
import { EraseDialog } from "../components/EraseDialog"
import { useAddNote, useCustomer, useNotes, useTimeline } from "../hooks"

export function CustomerDetailPage() {
  const { t } = useTranslation()
  const { customerId } = useParams({ from: "/app/customers/$customerId" })
  const { businessTimeZone, permissions } = useSession()
  const canSeeConsent = permissions.has("crm.consent.read")
  const customer = useCustomer(customerId)
  const [editing, setEditing] = useState(false)
  const [erasing, setErasing] = useState(false)
  const c = customer.data

  if (customer.error) return <QueryError error={customer.error} onRetry={() => customer.refetch()} notFound={t("crm.notFound")} />
  if (customer.isPending) return <Skeleton className="h-64 w-full" />
  if (!c) return <EmptyState title={t("crm.notFound")} />

  return (
    <>
      <PageHeader
        title={c.display_name}
        description={c.contact_points.map((p) => p.value).join(" · ") || undefined}
        actions={
          <div className="flex gap-2">
            <PermissionGate permission="crm.customer.write"><Button variant="outline" onClick={() => setEditing(true)}>{t("crm.edit")}</Button></PermissionGate>
            <PermissionGate permission="admin_ops.data.manage"><Button variant="ghost" onClick={() => setErasing(true)}>{t("crm.erase.open")}</Button></PermissionGate>
          </div>
        }
      />
      {c.tags.length ? <p className="mb-4 flex gap-1">{c.tags.map((tag) => <Badge key={tag} variant="outline">{tag}</Badge>)}</p> : null}
      <Tabs defaultValue="timeline">
        <TabsList>
          <TabsTrigger value="timeline">{t("crm.tabs.timeline")}</TabsTrigger>
          <TabsTrigger value="notes">{t("crm.tabs.notes")}</TabsTrigger>
          {canSeeConsent ? <TabsTrigger value="consent">{t("crm.tabs.consent")}</TabsTrigger> : null}
        </TabsList>
        <TabsContent value="timeline" className="pt-4"><Timeline customerId={customerId} timeZone={businessTimeZone} /></TabsContent>
        <TabsContent value="notes" className="pt-4"><Notes customerId={customerId} timeZone={businessTimeZone} /></TabsContent>
        {canSeeConsent ? <TabsContent value="consent" className="pt-4"><ConsentPanel customerId={customerId} /></TabsContent> : null}
      </Tabs>
      {erasing ? <EraseDialog customer={c} onOpenChange={setErasing} /> : null}
      {editing ? <CustomerDialog open onOpenChange={setEditing} customer={c} onReload={() => void customer.refetch()} /> : null}
    </>
  )
}

function Timeline({ customerId, timeZone }: { customerId: string; timeZone: string }) {
  const { t } = useTranslation()
  const timeline = useTimeline(customerId)
  if (timeline.error) return <QueryError error={timeline.error} onRetry={() => timeline.refetch()} />
  if (timeline.isPending) return <Skeleton className="h-32 w-full" />
  if (!timeline.data?.length) return <EmptyState title={t("crm.noTimeline")} />
  return (
    <ol className="space-y-3">
      {timeline.data.map((e) => (
        <li key={e.id} className="flex flex-wrap gap-3 text-sm">
          <span className="w-36 shrink-0 text-muted-foreground"><LocalTime iso={e.occurred_at} timeZone={timeZone} pattern="d MMM yyyy HH:mm" /></span>
          <Badge variant="secondary">{t(`crm.kinds.${e.kind}`, { defaultValue: e.kind })}</Badge>
          <span>{e.summary}</span>
        </li>
      ))}
    </ol>
  )
}

function Notes({ customerId, timeZone }: { customerId: string; timeZone: string }) {
  const { t } = useTranslation()
  const notes = useNotes(customerId)
  const add = useAddNote(customerId)
  const [body, setBody] = useState("")
  const [pinned, setPinned] = useState(false)
  return (
    <div className="space-y-4">
      <PermissionGate permission="crm.customer.write">
        <form className="space-y-2" onSubmit={(e) => {
          e.preventDefault()
          if (body.trim()) add.mutate({ body: body.trim(), pinned }, { onSuccess: () => { setBody(""); setPinned(false) }, onError: (err) => toastError(err) })
        }}>
          <Label htmlFor="note-body">{t("crm.notes.add")}</Label>
          <Textarea id="note-body" value={body} onChange={(e) => setBody(e.target.value)} />
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-2"><Checkbox id="note-pin" checked={pinned} onCheckedChange={(v) => setPinned(v === true)} /><Label htmlFor="note-pin">{t("crm.notes.pin")}</Label></span>
            <Button type="submit" disabled={add.isPending}>{t("common.add")}</Button>
          </div>
        </form>
      </PermissionGate>
      {notes.error ? <QueryError error={notes.error} onRetry={() => notes.refetch()} /> : notes.isPending ? <Skeleton className="h-24 w-full" /> : notes.data?.length ? (
        <ul className="space-y-2">
          {notes.data.map((n) => (
            <li key={n.id} className="rounded-md border p-3 text-sm">
              {n.pinned ? <Badge className="mb-1">{t("crm.notes.pinned")}</Badge> : null}
              <p className="whitespace-pre-wrap">{n.body}</p>
              <p className="mt-1 text-xs text-muted-foreground"><LocalTime iso={n.created_at} timeZone={timeZone} pattern="d MMM yyyy HH:mm" /></p>
            </li>
          ))}
        </ul>
      ) : <p className="text-sm text-muted-foreground">{t("crm.notes.empty")}</p>}
    </div>
  )
}
