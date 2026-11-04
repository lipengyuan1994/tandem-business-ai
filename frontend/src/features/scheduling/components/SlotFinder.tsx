import { QueryError } from "@/components/common/QueryError"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { Slot } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useCatalogItems } from "@/features/catalog/hooks"
import { useCustomerMatch } from "@/features/crm/hooks"
import { useSlots } from "../hooks"

export interface SlotChoice {
  service_id: string
  customer_id: string
  slot: Slot
}

/** Service + customer + day → the server's free slots. Calls `onPick`; the caller books or revises a proposal. */
export function SlotFinder({ localDate, timeZone, onPick, pending, error, initial, lockCustomer }: {
  localDate: string
  timeZone: string
  onPick: (choice: SlotChoice) => void
  pending?: boolean
  error?: string | null
  initial?: { service_id?: string; customer_id?: string; customer_name?: string }
  /** Show the customer read-only (e.g. when revising a proposal: only the time changes). */
  lockCustomer?: boolean
}) {
  const { t } = useTranslation()
  const services = useCatalogItems("service")
  const [serviceId, setServiceId] = useState(initial?.service_id ?? "")
  const [date, setDate] = useState(localDate)
  const [customerQuery, setCustomerQuery] = useState(initial?.customer_name ?? "")
  const [customerId, setCustomerId] = useState(initial?.customer_id ?? "")
  const matches = useCustomerMatch(customerId ? "" : customerQuery)
  const slots = useSlots(serviceId || undefined, date, timeZone)

  return (
    <div className="space-y-4">
      {services.error ? <QueryError error={services.error} onRetry={() => services.refetch()} /> : null}
      {matches.error ? <QueryError error={matches.error} onRetry={() => matches.refetch()} /> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="sf-service">{t("schedule.service")}</Label>
          <select id="sf-service" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={serviceId} onChange={(e) => setServiceId(e.target.value)}>
            <option value="">{t("schedule.chooseService")}</option>
            {services.data?.items.filter((s) => s.status === "active").map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="sf-date">{t("schedule.date")}</Label>
          <Input id="sf-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </div>
      </div>
      <div className="space-y-1">
        <Label htmlFor="sf-customer">{t("schedule.customer")}</Label>
        <Input id="sf-customer" value={customerQuery} placeholder={t("schedule.customerHint")} autoComplete="off" readOnly={lockCustomer}
          onChange={(e) => { setCustomerQuery(e.target.value); setCustomerId("") }} />
        {!customerId && matches.data?.length ? (
          <ul className="rounded-md border" aria-label={t("schedule.customerMatches")}>
            {matches.data.slice(0, 5).map((m) => (
              <li key={m.customer_id}>
                <button type="button" className="w-full px-3 py-1.5 text-left text-sm hover:bg-accent" onClick={() => { setCustomerId(m.customer_id); setCustomerQuery(m.display_name) }}>
                  {m.display_name}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      {error ? <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">{error}</p> : null}
      {serviceId ? (
        <section aria-labelledby="sf-slots-h" className="space-y-2">
          <h3 id="sf-slots-h" className="text-sm font-medium">{t("schedule.freeSlots")}</h3>
          {slots.error ? <QueryError error={slots.error} onRetry={() => slots.refetch()} /> : slots.isPending ? <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
            : !slots.data?.length ? <p className="text-sm text-muted-foreground">{t("schedule.noSlots")}</p> : (
              <ul className="flex flex-wrap gap-2">
                {slots.data.map((s) => (
                  <li key={`${s.resource_id}-${s.starts_at}`}>
                    <Button type="button" variant="outline" size="sm" disabled={!customerId || pending}
                      onClick={() => onPick({ service_id: serviceId, customer_id: customerId, slot: s })}>
                      {s.local_label} · {s.resource_name}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          {!customerId ? <p className="text-xs text-muted-foreground">{t("schedule.pickCustomerFirst")}</p> : null}
        </section>
      ) : null}
    </div>
  )
}
