import { formatQuantity as qty } from "@/lib/quantity"
import { QueryError } from "@/components/common/QueryError"
import { Link, useParams } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { AskAiQuotesButton } from "@/features/ai/AskAiQuotesButton"
import { AdjustDialog } from "./components/AdjustDialog"
import { useItemStock, withRunningBalance } from "./hooks"


export function InventoryItemPage() {
  const { t } = useTranslation()
  const { itemId } = useParams({ from: "/app/inventory/$itemId" })
  const { businessTimeZone } = useSession()
  const stock = useItemStock(itemId)
  const [adjusting, setAdjusting] = useState(false)

  if (stock.error) return <QueryError error={stock.error} onRetry={() => stock.refetch()} notFound={t("inventory.notFound")} />
  if (stock.isPending) return <Skeleton className="h-64 w-full" />
  const level = stock.data?.levels[0]
  if (!stock.data || !level) return <EmptyState title={t("inventory.notFound")} />
  const ledger = withRunningBalance(stock.data.movements, stock.data.levels.reduce((sum, l) => sum + Number(l.on_hand), 0)).reverse()
  const active = stock.data.reservations.filter((r) => r.status === "active")

  return (
    <>
      <PageHeader title={level.item_name} description={t("inventory.itemSummary", { onHand: qty(level.on_hand), reserved: qty(level.reserved), available: qty(level.available) })}
        actions={<PermissionGate permission="inventory.stock.adjust"><Button onClick={() => setAdjusting(true)}>{t("inventory.adjust.open")}</Button></PermissionGate>} />
      {stock.data.rule && Number(level.available) <= Number(stock.data.rule.reorder_point) ? <div className="mb-6"><AskAiQuotesButton itemId={itemId} /></div> : null}
      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <section aria-labelledby="ledger-h" className="space-y-2">
          <h2 id="ledger-h" className="font-medium">{t("inventory.ledger")}</h2>
          <div className="rounded-lg border">
            <Table>
              <TableHeader><TableRow>
                <TableHead>{t("inventory.fields.when")}</TableHead><TableHead>{t("inventory.fields.reason")}</TableHead>
                <TableHead className="text-right">{t("inventory.fields.change")}</TableHead><TableHead className="text-right">{t("inventory.fields.balance")}</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {ledger.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell><LocalTime iso={m.occurred_at} timeZone={businessTimeZone} pattern="d MMM yyyy HH:mm" /></TableCell>
                    <TableCell>{t(`inventory.reasons.${m.reason}`, { defaultValue: m.reason })}{m.note ? <span className="text-muted-foreground"> · {m.note}</span> : null}</TableCell>
                    <TableCell className="text-right tabular-nums">{Number(m.quantity) > 0 ? "+" : ""}{qty(m.quantity)}</TableCell>
                    <TableCell className="text-right tabular-nums">{qty(m.balance)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>
        <aside className="space-y-4">
          <Card>
            <CardHeader><CardTitle className="text-sm">{t("inventory.reservations")}</CardTitle></CardHeader>
            <CardContent>
              {active.length === 0 ? <p className="text-sm text-muted-foreground">{t("inventory.noReservations")}</p> : (
                <ul className="space-y-1 text-sm">
                  {active.map((r) => (
                    <li key={r.id}>{qty(r.quantity)} × {r.ref_type === "appointment"
                      ? <Link className="underline" to="/schedule" search={{ appointment: r.ref_id }}>{t("inventory.forAppointment")}</Link>
                      : r.ref_type}</li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
          {stock.data.rule ? (
            <Card>
              <CardHeader><CardTitle className="text-sm">{t("inventory.rule")}</CardTitle></CardHeader>
              <CardContent className="text-sm">{t("inventory.ruleLine", { point: qty(stock.data.rule.reorder_point), target: qty(stock.data.rule.target_level), days: stock.data.rule.lead_time_days })}</CardContent>
            </Card>
          ) : null}
        </aside>
      </div>
      {adjusting ? <AdjustDialog itemId={itemId} itemName={level.item_name} onOpenChange={setAdjusting} /> : null}
    </>
  )
}
