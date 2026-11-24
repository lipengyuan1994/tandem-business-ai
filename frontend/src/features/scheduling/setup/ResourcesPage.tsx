// Settings → Resources: the people, rooms and equipment that can be booked, their weekly hours, the services each can
// do, and closures. Without these the slot finder has nothing to offer, so a new business sets them up here.
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { Resource } from "@/api/types"
import { useSession } from "@/app/session-context"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { EmptyState } from "@/components/common/EmptyState"
import { QueryError } from "@/components/common/QueryError"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { toastError } from "@/lib/errors"
import { useLocations } from "@/features/org/hooks"
import { useAllResources, useCreateResource, useUpdateResource } from "../hooks"
import { ClosuresCard } from "./ClosuresCard"
import { ResourceServices } from "./ResourceServices"
import { WeeklyHours } from "./WeeklyHours"

const KINDS = ["staff", "room", "equipment"] as const
type Kind = (typeof KINDS)[number]

function NewResourceForm({ onCreated }: { onCreated: (id: string) => void }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const create = useCreateResource()
  const locations = useLocations()
  const [name, setName] = useState("")
  const [kind, setKind] = useState<Kind>("staff")
  const [locationId, setLocationId] = useState("")
  const several = permissions.has("org.location.read") && (locations.data?.length ?? 0) > 1
  return (
    <form className="space-y-2 rounded-md border p-3" aria-label={t("resources.new")}
      onSubmit={(e) => {
        e.preventDefault()
        if (!name.trim()) return
        create.mutate({ name: name.trim(), kind, location_id: locationId || null }, {
          onSuccess: (r) => { setName(""); toast.success(t("resources.created", { name: r.name })); onCreated(r.id) },
          onError: (err) => toastError(err),
        })
      }}>
      <div className="space-y-1">
        <Label htmlFor="new-resource-name">{t("resources.name")}</Label>
        <Input id="new-resource-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="new-resource-kind">{t("resources.kind")}</Label>
        <select id="new-resource-kind" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
          {KINDS.map((k) => <option key={k} value={k}>{t(`resources.kinds.${k}`)}</option>)}
        </select>
      </div>
      {several ? (
        <div className="space-y-1">
          <Label htmlFor="new-resource-location">{t("resources.location")}</Label>
          <select id="new-resource-location" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={locationId} onChange={(e) => setLocationId(e.target.value)}>
            <option value="">{t("resources.defaultLocation")}</option>
            {locations.data?.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </div>
      ) : null}
      <Button type="submit" variant="outline" disabled={create.isPending || !name.trim()}>{t("resources.add")}</Button>
    </form>
  )
}

function ResourceDetails({ resource, canEdit }: { resource: Resource; canEdit: boolean }) {
  const { t } = useTranslation()
  const update = useUpdateResource(resource.id)
  const [name, setName] = useState(resource.name)
  const [conflict, setConflict] = useState(false)
  const archived = resource.status === "archived"
  const save = (body: { name?: string; status?: "active" | "archived" }, done: string) =>
    update.mutate({ version: resource.version, ...body }, { onSuccess: () => toast.success(done), onError: (e) => toastError(e, () => setConflict(true)) })
  return (
    <div className="space-y-3">
      <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim() && name.trim() !== resource.name) save({ name: name.trim() }, t("common.saved")) }}>
        <div className="min-w-48 flex-1 space-y-1">
          <Label htmlFor="resource-name">{t("resources.name")}</Label>
          <Input id="resource-name" value={name} disabled={!canEdit} maxLength={120} onChange={(e) => setName(e.target.value)} />
        </div>
        {canEdit ? <Button type="submit" variant="outline" disabled={update.isPending || !name.trim() || name.trim() === resource.name}>{t("resources.rename")}</Button> : null}
      </form>
      <p className="text-sm text-muted-foreground">{t(`resources.kinds.${resource.kind}`, { defaultValue: resource.kind })}{archived ? ` · ${t("resources.archivedHint")}` : ""}</p>
      {canEdit ? (
        archived
          ? <Button variant="outline" disabled={update.isPending} onClick={() => save({ status: "active" }, t("resources.restored", { name: resource.name }))}>{t("resources.restore")}</Button>
          : <Button variant="outline" disabled={update.isPending} onClick={() => save({ status: "archived" }, t("resources.archived", { name: resource.name }))}>{t("resources.archive")}</Button>
      ) : null}
      <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => setConflict(false)} />
    </div>
  )
}

export function ResourcesPage() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const resources = useAllResources()
  const [selected, setSelected] = useState<string | undefined>()
  const canEdit = permissions.has("scheduling.resource.write")

  if (resources.error) return <QueryError error={resources.error} onRetry={() => resources.refetch()} />
  if (resources.isPending) return <Skeleton className="h-64 w-full" />
  const list = [...resources.data].sort((a, b) => Number(a.status === "archived") - Number(b.status === "archived") || a.name.localeCompare(b.name))
  const current = list.find((r) => r.id === selected) ?? list[0]
  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">{t("resources.intro")}</p>
      <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
        <div className="space-y-3">
          {list.length === 0 ? <EmptyState title={t("resources.none")} /> : (
            <ul className="space-y-1" aria-label={t("resources.list")}>
              {list.map((r) => (
                <li key={r.id}>
                  <Button variant={r.id === current?.id ? "secondary" : "ghost"} className="w-full justify-between" aria-pressed={r.id === current?.id} onClick={() => setSelected(r.id)}>
                    <span className="truncate">{r.name}</span>
                    {r.status === "archived" ? <Badge variant="outline">{t("status.archived")}</Badge> : null}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {canEdit ? <NewResourceForm onCreated={setSelected} /> : null}
        </div>
        {current ? (
          <div className="space-y-6">
            <Card>
              <CardHeader><CardTitle className="text-base">{current.name}</CardTitle></CardHeader>
              <CardContent><ResourceDetails key={`${current.id}-${current.version}`} resource={current} canEdit={canEdit} /></CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-base">{t("resources.hours.title")}</CardTitle></CardHeader>
              <CardContent><WeeklyHours key={`${current.id}-${JSON.stringify(current.rules)}`} resource={current} canEdit={canEdit} /></CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-base">{t("resources.services.title")}</CardTitle></CardHeader>
              <CardContent><ResourceServices key={`${current.id}-${current.service_ids.join(",")}`} resource={current} others={list} canEdit={canEdit} /></CardContent>
            </Card>
          </div>
        ) : null}
      </div>
      <ClosuresCard resources={list} canEdit={canEdit} />
    </div>
  )
}
