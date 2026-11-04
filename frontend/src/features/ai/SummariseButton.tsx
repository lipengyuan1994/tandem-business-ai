// "Summarise" in the Inbox thread: a short summary, the open questions, and a reply draft that waits in Approvals.
import { Link } from "@tanstack/react-router"
import { Sparkles } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ApiProblem } from "@/lib/problem"
import { problemCode, useThreadSummary } from "./api"
import { AiDisabledBanner } from "./AiDisabledBanner"
import { aiErrorText, disabledText, useAiAvailability } from "./text"

export function SummariseButton({ conversationId }: { conversationId: string }) {
  const { permissions } = useSession()
  if (!permissions.has("ai.assistant.use")) return null
  return <SummariseControl conversationId={conversationId} />
}

function SummariseControl({ conversationId }: { conversationId: string }) {
  const { t } = useTranslation()
  const { status, enabled } = useAiAvailability()
  const summary = useThreadSummary(conversationId)
  const [open, setOpen] = useState(false)
  const result = summary.data

  const run = () => {
    setOpen(true)
    summary.mutate()
  }
  return (
    <>
      <Button size="sm" variant="outline" disabled={!enabled || summary.isPending} title={status && !enabled ? disabledText(t, status.reason) : undefined} onClick={run}>
        <Sparkles aria-hidden="true" /> {summary.isPending ? t("ai.summary.working") : t("ai.summary.button")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("ai.summary.title")}</DialogTitle>
            <DialogDescription>{t("ai.mistakes")}</DialogDescription>
          </DialogHeader>
          <AiDisabledBanner />
          {summary.isPending ? <p role="status">{t("ai.summary.working")}</p> : null}
          {summary.error ? <p role="alert" className="text-sm">{aiErrorText(t, summary.error instanceof ApiProblem ? problemCode(summary.error) : null)}</p> : null}
          {result ? (
            <div className="space-y-4 text-sm" aria-live="polite">
              <p className="whitespace-pre-wrap break-words">{result.summary.summary}</p>
              <section aria-labelledby="summary-questions" className="space-y-1">
                <h3 id="summary-questions" className="font-medium">{t("ai.summary.openQuestions")}</h3>
                {result.summary.open_questions.length ? <ul className="list-disc space-y-1 pl-5">{result.summary.open_questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
                  : <p className="text-muted-foreground">{t("ai.summary.noQuestions")}</p>}
              </section>
              {result.proposal_id ? (
                <p className="rounded-md border p-3">
                  {t("ai.summary.draftCreated")}{" "}
                  <Link className="record-link" to="/approvals/$proposalId" params={{ proposalId: result.proposal_id }}>{t("ai.summary.review")}</Link>
                </p>
              ) : <p className="text-muted-foreground">{t("ai.summary.noDraft")}</p>}
            </div>
          ) : null}
          <DialogFooter><Button variant="outline" onClick={() => setOpen(false)}>{t("common.close")}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
