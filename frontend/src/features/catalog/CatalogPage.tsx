import { QueryError } from "@/components/common/QueryError"
import type { ColumnDef } from "@tanstack/react-table"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import type { CatalogItem, CatalogItemCreate } from "@/api/types"
import { DataTable } from "@/components/common/DataTable"
import { MoneyText } from "@/components/common/MoneyText"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useBusiness } from "@/features/org/hooks"
import { parseMoneyInput } from "@/lib/money"
import { toastError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"
import { useCatalogItems, useCreateItem } from "./hooks"

type Kind = "service" | "product"

export function CatalogPage() {
  const { t } = useTranslation()
  const [kind, setKind] = useState<Kind>("service")
  const [creating, setCreating] = useState(false)
  const items = useCatalogItems(kind)

  const columns = useMemo<ColumnDef<CatalogItem, unknown>[]>(() => [
    { header: t("catalog.fields.name"), cell: ({ row }) => <span className="font-medium">{row.original.name}</span> },
    { header: t("catalog.fields.price"), cell: ({ row }) => row.original.price_minor === null ? t("catalog.onRequest")
      : <MoneyText value={{ amount_minor: row.original.price_minor, currency: row.original.currency }} /> },
    kind === "service"
      ? { header: t("catalog.fields.duration"), cell: ({ row }) => t("catalog.minutes", { n: row.original.duration_minutes }) }
      : { header: t("catalog.fields.tracked"), cell: ({ row }) => (row.original.is_stock_tracked ? t("catalog.yes") : t("catalog.no")) },
    { header: t("catalog.fields.status"), cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { header: t("catalog.fields.sku"), cell: ({ row }) => row.original.sku ?? "—" },
  ], [t, kind])

  return (
    <>
      <PageHeader title={t("nav.catalog")} description={t("catalog.description")}
        actions={<PermissionGate permission="catalog.item.write"><Button onClick={() => setCreating(true)}>{t(`catalog.new.${kind}`)}</Button></PermissionGate>} />
      <Tabs value={kind} onValueChange={(v) => setKind(v as Kind)} className="mb-4">
        <TabsList>
          <TabsTrigger value="service">{t("catalog.tabs.services")}</TabsTrigger>
          <TabsTrigger value="product">{t("catalog.tabs.products")}</TabsTrigger>
        </TabsList>
        <TabsContent value={kind}>
          <DataTable columns={columns} data={items.data?.items} isLoading={items.isPending} error={items.error} onRetry={() => items.refetch()} caption={t("nav.catalog")} />
        </TabsContent>
      </Tabs>
      {creating ? <ItemDialog kind={kind} onOpenChange={setCreating} /> : null}
    </>
  )
}

function ItemDialog({ kind, onOpenChange }: { kind: Kind; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const create = useCreateItem()
  const business = useBusiness()
  const [name, setName] = useState("")
  const [price, setPrice] = useState("")
  const [duration, setDuration] = useState("60")
  const [errors, setErrors] = useState<Record<string, string>>({})

  const submit = () => {
    if (!business.data) return
    const parsed = price.trim() ? parseMoneyInput(price, business.data.currency) : null
    if (parsed && !parsed.ok) {
      setErrors({ price_minor: t(`sales.priceErrors.${parsed.error}`, { currency: business.data.currency, digits: parsed.digits }) })
      return
    }
    setErrors({})
    const body: CatalogItemCreate = {
      kind, name: name.trim(), sku: null, description: null, unit: "each",
      price_minor: parsed?.ok ? parsed.minor : null,
      duration_minutes: kind === "service" ? Number(duration) : null, buffer_minutes: 0,
      resource_kind: kind === "service" ? "staff" : null, is_stock_tracked: kind === "product", is_bookable_online: kind === "service",
    }
    create.mutate(body, {
      onSuccess: () => onOpenChange(false),
      onError: (e) => { if (e instanceof ApiProblem) setErrors(e.fieldErrors()); toastError(e) },
    })
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t(`catalog.new.${kind}`)}</DialogTitle>
          <DialogDescription>{t("catalog.dialogHint")}</DialogDescription>
        </DialogHeader>
        {business.error ? <QueryError error={business.error} onRetry={() => business.refetch()} /> : null}
        <form id="item-form" className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit() }}>
          <div className="space-y-1">
            <Label htmlFor="i-name">{t("catalog.fields.name")}</Label>
            <Input id="i-name" required value={name} aria-invalid={!!errors.name} aria-describedby={errors.name ? "i-name-error" : undefined} onChange={(e) => setName(e.target.value)} />
            {errors.name ? <p id="i-name-error" role="alert" className="text-sm text-destructive">{errors.name}</p> : null}
          </div>
          <div className="space-y-1">
            <Label htmlFor="i-price">{t("catalog.fields.priceInput")}</Label>
            <Input id="i-price" inputMode="decimal" value={price} aria-invalid={!!errors.price_minor} aria-describedby={errors.price_minor ? "i-price-error" : undefined} onChange={(e) => setPrice(e.target.value)} />
            {errors.price_minor ? <p id="i-price-error" role="alert" className="text-sm text-destructive">{errors.price_minor}</p> : null}
          </div>
          {kind === "service" ? (
            <div className="space-y-1">
              <Label htmlFor="i-duration">{t("catalog.fields.durationInput")}</Label>
              <Input id="i-duration" type="number" min={5} max={1440} value={duration} aria-invalid={!!errors.duration_minutes} aria-describedby={errors.duration_minutes ? "i-duration-error" : undefined} onChange={(e) => setDuration(e.target.value)} />
              {errors.duration_minutes ? <p id="i-duration-error" role="alert" className="text-sm text-destructive">{errors.duration_minutes}</p> : null}
            </div>
          ) : null}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button type="submit" form="item-form" disabled={create.isPending || !business.data}>{t("common.save")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
