import { ArrowRight } from "lucide-react"
import { useTranslation } from "react-i18next"
import type { Schemas } from "@/api/types"

/** The hero of the approval screen: exactly what will change, in plain words (never the raw payload). */
export function PreviewDiff({ lines }: { lines: Schemas["PreviewLineOut"][] }) {
  const { t } = useTranslation()
  return (
    <dl className="divide-y rounded-lg border">
      {lines.map((line, i) => (
        <div key={`${line.label}-${i}`} className="grid gap-1 p-3 sm:grid-cols-[10rem_1fr]">
          <dt className="text-sm font-medium">{line.label}</dt>
          <dd className="flex flex-wrap items-center gap-2 text-sm">
            {line.before !== null && line.before !== undefined ? (
              <>
                <span className="text-muted-foreground line-through" aria-label={t("approvals.before")}>{line.before}</span>
                <ArrowRight className="size-4 text-muted-foreground" aria-hidden="true" />
              </>
            ) : (
              <span className="rounded bg-status-success/15 px-1.5 text-xs">{t("approvals.new")}</span>
            )}
            <span className="font-medium">{line.after ?? "—"}</span>
          </dd>
        </div>
      ))}
    </dl>
  )
}
