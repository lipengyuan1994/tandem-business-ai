import type { ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { downloadCsv, type CsvCell, type RangeInput } from "../model"

const SELECT = "h-9 w-full rounded-md border bg-background px-3 text-sm"

export function DateField({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (value: string) => void }) {
  return <div className="space-y-1"><Label htmlFor={id}>{label}</Label>
    <Input id={id} type="date" value={value} onChange={(e) => onChange(e.target.value)} /></div>
}

export function RangeFields({ id, range, onChange }: { id: string; range: RangeInput; onChange: (range: RangeInput) => void }) {
  const { t } = useTranslation()
  return <>
    <DateField id={`${id}-from`} label={t("reports.filters.from")} value={range.from} onChange={(from) => onChange({ ...range, from })} />
    <DateField id={`${id}-to`} label={t("reports.filters.to")} value={range.to} onChange={(to) => onChange({ ...range, to })} />
  </>
}

export function ChoiceField<T extends string>({ id, label, value, options, onChange }: {
  id: string; label: string; value: T; options: { value: T; label: string }[]; onChange: (value: T) => void
}) {
  return <div className="min-w-36 space-y-1"><Label htmlFor={id}>{label}</Label>
    <select id={id} className={SELECT} value={value} onChange={(e) => onChange(e.target.value as T)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select></div>
}

export function Toolbar({ children, csv }: { children: ReactNode; csv?: { filename: string; rows: () => CsvCell[][] } | null }) {
  const { t } = useTranslation()
  return <div className="flex flex-wrap items-end gap-3">
    {children}
    {csv ? <Button type="button" variant="outline" onClick={() => downloadCsv(csv.filename, csv.rows())}>{t("reports.download")}</Button> : null}
  </div>
}

/** Loading, error and empty states shared by every report. */
export function ReportBody<T>({ query, isEmpty, children }: {
  query: { isPending: boolean; error: Error | null; data: T | undefined; refetch: () => unknown }
  isEmpty: (data: T) => boolean
  children: (data: T) => ReactNode
}) {
  const { t } = useTranslation()
  if (query.isPending) return <Skeleton className="h-40 w-full" />
  if (query.error) return <QueryError error={query.error} onRetry={() => void query.refetch()} />
  if (query.data === undefined || isEmpty(query.data)) return <p className="text-sm">{t("reports.empty")}</p>
  return <>{children(query.data)}</>
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>
}
