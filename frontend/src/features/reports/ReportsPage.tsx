import { useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { EmptyState } from "@/components/common/EmptyState"
import { PageHeader } from "@/components/common/PageHeader"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { RangeFields } from "./components/controls"
import { MarginReport } from "./components/MarginReport"
import { SalesReport } from "./components/SalesReport"
import { SpendReport } from "./components/SpendReport"
import { StockValueReport } from "./components/StockValueReport"
import { availableReports, rangeError, type RangeInput, type ReportKind } from "./model"

/** Read-only business reports. Each tab shows only when the user can read the data behind it. */
export function ReportsPage() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const reports = availableReports(permissions)
  // One date range for every dated report, so switching tabs compares the same period.
  const [range, setRange] = useState<RangeInput>({ from: "", to: "" })
  if (!reports.length) return <EmptyState title={t("reports.noAccess")} />
  const dated = (id: string, report: ReactNode) => <>
    {rangeError(range) ? <>
      <div className="flex flex-wrap items-end gap-3"><RangeFields id={id} range={range} onChange={setRange} /></div>
      <p role="alert" className="text-sm text-destructive">{t("reports.invalidRange")}</p>
    </> : report}
    <p className="text-sm text-muted-foreground">{t("reports.filters.hint")}</p>
  </>
  const content: Record<ReportKind, () => ReactNode> = {
    sales: () => dated("sales", <SalesReport range={range} onRangeChange={setRange} />),
    margin: () => dated("margin", <MarginReport range={range} onRangeChange={setRange} />),
    stock: () => <StockValueReport />,
    spend: () => dated("spend", <SpendReport range={range} onRangeChange={setRange} />),
  }
  return <>
    <PageHeader title={t("reports.title")} description={t("reports.description")} />
    <Tabs defaultValue={reports[0]}>
      <TabsList aria-label={t("reports.title")}>{reports.map((r) => <TabsTrigger key={r} value={r}>{t(`reports.tabs.${r}`)}</TabsTrigger>)}</TabsList>
      {reports.map((r) => <TabsContent key={r} value={r} className="space-y-4 pt-4">{content[r]()}</TabsContent>)}
    </Tabs>
  </>
}
