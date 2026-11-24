import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { CatalogItem, Resource } from "@/api/types"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toastError } from "@/lib/errors"
import { useCatalogItems } from "@/features/catalog/hooks"
import { useReplaceResourceServices } from "../hooks"

/** Kinds of resource a service needs: its required roles, or its single resource kind. */
function kindsNeeded(s: CatalogItem): string[] {
  const roles = s.required_resources?.map((r) => r.kind) ?? []
  return roles.length ? roles : s.resource_kind ? [s.resource_kind] : []
}

/** Which services this resource can do. The slot finder only offers a resource for the services ticked here. */
export function ResourceServices({ resource, canEdit }: { resource: Resource; canEdit: boolean }) {
  const { t } = useTranslation()
  const services = useCatalogItems("service")
  const replace = useReplaceResourceServices(resource.id)
  const [ticked, setTicked] = useState<Set<string>>(() => new Set(resource.service_ids))

  if (services.error) return <QueryError error={services.error} onRetry={() => services.refetch()} />
  if (services.isPending) return <Skeleton className="h-24 w-full" />
  const active = services.data.items.filter((s) => s.status === "active")
  if (active.length === 0) return <p className="text-sm text-muted-foreground">{t("resources.services.none")}</p>
  const toggle = (id: string, on: boolean) => {
    const next = new Set(ticked)
    if (on) next.add(id)
    else next.delete(id)
    setTicked(next)
  }
  const unchanged = ticked.size === resource.service_ids.length && resource.service_ids.every((id) => ticked.has(id))
  return (
    <div className="space-y-3">
      <fieldset className="space-y-2">
        <legend className="mb-2 text-sm text-muted-foreground">{t("resources.services.hint")}</legend>
        {active.map((s) => {
          const kinds = kindsNeeded(s)
          const mismatch = kinds.length > 0 && !kinds.includes(resource.kind)
          return (
            <label key={s.id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="size-4" checked={ticked.has(s.id)} disabled={!canEdit} onChange={(e) => toggle(s.id, e.target.checked)} />
              <span>{s.name}</span>
              {mismatch ? <span className="text-xs text-muted-foreground">({t("resources.services.needs", { kinds: kinds.map((k) => t(`resources.kinds.${k}`, { defaultValue: k })).join(", ") })})</span> : null}
            </label>
          )
        })}
      </fieldset>
      {canEdit ? (
        <Button disabled={replace.isPending || unchanged} onClick={() => replace.mutate([...ticked], { onSuccess: () => toast.success(t("resources.services.saved")), onError: (e) => toastError(e) })}>
          {t("resources.services.save")}
        </Button>
      ) : null}
    </div>
  )
}
