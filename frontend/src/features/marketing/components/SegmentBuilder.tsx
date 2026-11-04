import { QueryError } from "@/components/common/QueryError"
import { X } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { Clause } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useCatalogItems } from "@/features/catalog/hooks"

type Kind = "tag" | "last_interaction_older" | "last_interaction_newer" | "created_older" | "created_newer" | "customer_kind" | "service"

function clauseFor(kind: Kind, value: string): Clause {
  switch (kind) {
    case "tag": return { field: "tag", op: "has", value: value.trim() }
    case "last_interaction_older": return { field: "last_interaction_at", op: "older_than_days", value: Number(value) }
    case "last_interaction_newer": return { field: "last_interaction_at", op: "newer_than_days", value: Number(value) }
    case "created_older": return { field: "created_at", op: "older_than_days", value: Number(value) }
    case "created_newer": return { field: "created_at", op: "newer_than_days", value: Number(value) }
    case "customer_kind": return { field: "customer_kind", op: "eq", value }
    case "service": return { field: "has_appointment_for_service", op: "eq", value }
  }
}

const isDays = (k: Kind) => k.startsWith("last_") || k.startsWith("created_")

/** Chips for the whitelisted filters only (M12): there is no free-form query. All filters must match. */
export function SegmentBuilder({ clauses, onChange }: { clauses: Clause[]; onChange: (c: Clause[]) => void }) {
  const { t } = useTranslation()
  const services = useCatalogItems("service")
  const [kind, setKind] = useState<Kind>("tag")
  const [value, setValue] = useState("")
  const valid = isDays(kind) ? Number.isInteger(Number(value)) && Number(value) >= 1 && Number(value) <= 3650 : value.trim().length > 0

  const describe = (c: Clause): string => {
    if (c.field === "tag") return t("marketing.filters.chip.tag", { value: c.value })
    if (c.field === "customer_kind") return t(`marketing.filters.chip.kind.${c.value}`)
    if (c.field === "has_appointment_for_service") {
      const name = services.data?.items.find((s) => s.id === c.value)?.name ?? String(c.value)
      return t("marketing.filters.chip.service", { value: name })
    }
    return t(`marketing.filters.chip.${c.field}.${c.op}`, { value: c.value })
  }

  return (
    <div className="space-y-3">
      {services.error ? <QueryError error={services.error} onRetry={() => services.refetch()} /> : null}
      <ul className="flex flex-wrap gap-2" aria-label={t("marketing.filters.active")}>
        {clauses.length === 0 ? <li className="text-sm text-muted-foreground">{t("marketing.filters.none")}</li> : null}
        {clauses.map((c, i) => (
          <li key={`${c.field}-${c.op}-${String(c.value)}`}>
            <Badge variant="secondary" className="gap-1">
              {describe(c)}
              <button type="button" aria-label={t("marketing.filters.remove", { filter: describe(c) })} onClick={() => onChange(clauses.filter((_, j) => j !== i))}>
                <X className="size-3" aria-hidden="true" />
              </button>
            </Badge>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor="filter-kind">{t("marketing.filters.add")}</Label>
          <select id="filter-kind" className="h-9 rounded-md border bg-background px-3 text-sm" value={kind} onChange={(e) => { setKind(e.target.value as Kind); setValue("") }}>
            {(["tag", "last_interaction_older", "last_interaction_newer", "created_older", "created_newer", "customer_kind", "service"] as const).map((k) => (
              <option key={k} value={k}>{t(`marketing.filters.kinds.${k}`)}</option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="filter-value">{isDays(kind) ? t("marketing.filters.days") : t("marketing.filters.value")}</Label>
          {kind === "customer_kind" ? (
            <select id="filter-value" className="h-9 rounded-md border bg-background px-3 text-sm" value={value} onChange={(e) => setValue(e.target.value)}>
              <option value="">{t("marketing.filters.choose")}</option>
              <option value="person">{t("marketing.filters.chip.kind.person")}</option>
              <option value="organization">{t("marketing.filters.chip.kind.organization")}</option>
            </select>
          ) : kind === "service" ? (
            <select id="filter-value" className="h-9 rounded-md border bg-background px-3 text-sm" value={value} onChange={(e) => setValue(e.target.value)}>
              <option value="">{t("marketing.filters.choose")}</option>
              {services.data?.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          ) : (
            <Input id="filter-value" value={value} inputMode={isDays(kind) ? "numeric" : "text"} onChange={(e) => setValue(e.target.value)} />
          )}
        </div>
        <Button type="button" variant="outline" disabled={!valid || clauses.length >= 10} onClick={() => { onChange([...clauses, clauseFor(kind, value)]); setValue("") }}>
          {t("marketing.filters.addButton")}
        </Button>
      </div>
    </div>
  )
}
