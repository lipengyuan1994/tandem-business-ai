import { formatMoney, type Money } from "@/lib/money"

export function MoneyText({ value, locale }: { value: Money | null | undefined; locale?: string }) {
  if (!value) return <span className="text-muted-foreground">—</span>
  return <span className="whitespace-nowrap tabular-nums">{formatMoney(value, locale)}</span>
}
