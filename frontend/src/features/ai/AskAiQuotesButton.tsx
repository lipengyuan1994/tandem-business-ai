// "Ask AI to request quotes" on a low-stock item with at least two suppliers that can be e-mailed.
// The AI drafts the quote request; it waits in Approvals and nobody is contacted before a person approves.
import { Link } from "@tanstack/react-router"
import { Sparkles } from "lucide-react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { useSuppliers } from "@/features/purchasing/hooks"
import { ApiProblem } from "@/lib/problem"
import { problemCode, useRequestQuotes } from "./api"
import { aiErrorText, disabledText, useAiAvailability } from "./text"

export function AskAiQuotesButton({ itemId }: { itemId: string }) {
  const { permissions } = useSession()
  if (!permissions.has("ai.assistant.use") || !permissions.has("purchasing.po.write") || !permissions.has("purchasing.po.read")) return null
  return <AskAiQuotes itemId={itemId} />
}

function AskAiQuotes({ itemId }: { itemId: string }) {
  const { t } = useTranslation()
  const { status, enabled } = useAiAvailability()
  const suppliers = useSuppliers()
  const ask = useRequestQuotes(itemId)
  const count = (suppliers.data ?? []).filter((s) => s.email && s.items?.some((i) => i.catalog_item_id === itemId)).length
  if (count < 2) return null
  const ids = ask.data?.proposal_ids ?? []
  return (
    <section aria-label={t("ai.quotes.button")} className="space-y-2 rounded-lg border p-3 text-sm">
      <p className="text-muted-foreground">{t("ai.quotes.hint", { count })}</p>
      <Button variant="outline" disabled={!enabled || ask.isPending} title={status && !enabled ? disabledText(t, status.reason) : undefined} onClick={() => ask.mutate()}>
        <Sparkles aria-hidden="true" /> {ask.isPending ? t("ai.quotes.working") : t("ai.quotes.button")}
      </Button>
      <div aria-live="polite">
        {ask.error ? <p role="alert">{aiErrorText(t, ask.error instanceof ApiProblem ? problemCode(ask.error) : null)}</p> : null}
        {ask.data ? (ids.length ? (
          <p>{t("ai.quotes.done")}{" "}
            {ids.map((id, i) => <Link key={id} className="record-link mr-2" to="/approvals/$proposalId" params={{ proposalId: id }}>{t("ai.quotes.review", { n: i + 1 })}</Link>)}
          </p>
        ) : <p>{t("ai.quotes.none")}</p>) : null}
      </div>
    </section>
  )
}
