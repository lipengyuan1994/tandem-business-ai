// Today's briefing: up to five plain sentences written by the AI from your own numbers. Hidden when AI is off.
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { formatInZone } from "@/lib/dates"
import { ApiProblem } from "@/lib/problem"
import { problemCode, useBriefing } from "./api"
import { aiErrorText, useAiAvailability } from "./text"

export function BriefingCard({ className }: { className?: string }) {
  const { permissions } = useSession()
  if (!permissions.has("ai.assistant.use")) return null
  return <BriefingContent className={className} />
}

function BriefingContent({ className }: { className?: string }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const { enabled } = useAiAvailability()
  const briefing = useBriefing(enabled)
  if (!enabled) return null
  const error = briefing.error
  return (
    <Card className={className}>
      <CardHeader><CardTitle className="text-sm">{t("ai.briefing.title")}</CardTitle></CardHeader>
      <CardContent className="space-y-2">
        {briefing.isPending ? <Skeleton className="h-16 w-full" /> : error ? (
          <p role="alert" className="text-sm">{error instanceof ApiProblem ? aiErrorText(t, problemCode(error)) : t("ai.errors.generic")}</p>
        ) : briefing.data ? (
          <>
            <ul className="list-disc space-y-1 pl-5 text-sm">{briefing.data.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul>
            <p className="text-xs text-muted-foreground">{t("ai.briefing.updated", { time: formatInZone(briefing.data.generated_at, businessTimeZone, "HH:mm") })}</p>
          </>
        ) : null}
      </CardContent>
    </Card>
  )
}
