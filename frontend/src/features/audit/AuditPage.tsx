import { readableAction } from "@/lib/action-label"
import type { ColumnDef } from "@tanstack/react-table"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import type { AuditEvent } from "@/api/types"
import { useSession } from "@/app/session-context"
import { DataTable } from "@/components/common/DataTable"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { type AuditFilters, exportUrl, useAuditEvents } from "./hooks"

export function AuditPage() {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const [filters, setFilters] = useState<AuditFilters>({})
  const [selected, setSelected] = useState<AuditEvent | null>(null)
  const events = useAuditEvents(filters)
  const rows = events.data?.pages.flatMap((p) => p.items)

  const columns = useMemo<ColumnDef<AuditEvent, unknown>[]>(() => [
    { header: t("audit.summary"), accessorKey: "summary" },
    { header: t("audit.actor"), accessorKey: "actor_name" },
    { header: t("audit.action"), cell: ({ row }) => <span className="block min-w-48"><span className="block">{readableAction(row.original.action)}</span><code className="text-sm text-muted-foreground">{row.original.action}</code></span> },
    { header: t("audit.when"), cell: ({ row }) => <span className="whitespace-nowrap"><LocalTime iso={row.original.occurred_at} timeZone={businessTimeZone} pattern="d MMM yyyy HH:mm:ss" /></span> },
  ], [t, businessTimeZone])

  return (
    <>
      <PageHeader
        title={t("nav.audit")}
        description={t("audit.description")}
        actions={<PermissionGate permission="audit.export"><Button asChild variant="outline"><a href={exportUrl(filters)} download>{t("audit.export")}</a></Button></PermissionGate>}
      />
      <form className="mb-4 flex flex-wrap items-end gap-3" onSubmit={(e) => e.preventDefault()}>
        <div className="space-y-1">
          <Label htmlFor="f-action">{t("audit.filterAction")}</Label>
          <Input id="f-action" aria-describedby="f-action-help" placeholder="crm." value={filters.action_prefix ?? ""} onChange={(e) => setFilters({ ...filters, action_prefix: e.target.value })} />
          <p id="f-action-help" className="max-w-sm text-sm text-muted-foreground">{t("audit.filterHelp")}</p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="f-actor">{t("audit.filterActor")}</Label>
          <select id="f-actor" className="h-9 rounded-md border bg-background px-3 text-sm" value={filters.actor_kind ?? ""} onChange={(e) => setFilters({ ...filters, actor_kind: e.target.value })}>
            <option value="">{t("audit.anyActor")}</option>
            {["user", "assistant", "system", "visitor"].map((k) => <option key={k} value={k}>{t(`audit.actors.${k}`)}</option>)}
          </select>
        </div>
      </form>
      <DataTable
        columns={columns}
        data={rows}
        isLoading={events.isPending}
        error={events.error}
        onRetry={() => events.refetch()}
        hasMore={events.hasNextPage}
        onLoadMore={() => events.fetchNextPage()}
        onRowClick={setSelected}
        caption={t("nav.audit")}
      />
      <Sheet open={selected !== null} onOpenChange={(open) => !open && setSelected(null)}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{selected?.summary}</SheetTitle>
            <SheetDescription>{selected?.action}</SheetDescription>
          </SheetHeader>
          {selected ? (
            <dl className="space-y-2 px-4 text-sm">
              <div><dt className="text-muted-foreground">{t("audit.actor")}</dt><dd>{selected.actor_name}</dd></div>
              <div><dt className="text-muted-foreground">{t("audit.target")}</dt><dd><code className="text-xs">{selected.target_type} {selected.target_id}</code></dd></div>
              {selected.proposal_id ? <div><dt className="text-muted-foreground">{t("audit.proposal")}</dt><dd><a className="underline" href={`/approvals/${selected.proposal_id}`}>{selected.proposal_id}</a></dd></div> : null}
              <div><dt className="text-muted-foreground">{t("audit.changes")}</dt><dd><pre className="overflow-auto rounded bg-muted p-2 text-xs">{JSON.stringify(selected.changes, null, 2)}</pre></dd></div>
              <div><dt className="text-muted-foreground">{t("audit.requestId")}</dt><dd><code className="text-xs">{selected.request_id}</code></dd></div>
            </dl>
          ) : null}
        </SheetContent>
      </Sheet>
    </>
  )
}
