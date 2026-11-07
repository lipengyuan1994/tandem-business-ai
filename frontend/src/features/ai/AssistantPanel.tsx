// The assistant: a slide-over on the right (full screen on phones). Streams the steps it takes, links to the proposals
// it makes, with explicit revision-bound confirmation on saved action cards.
import { Link } from "@tanstack/react-router"
import { Sparkles } from "lucide-react"
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { SyntheticDemoBanner } from "@/components/common/SyntheticDemoBanner"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import { AiDisabledBanner } from "./AiDisabledBanner"
import { problemCode, useCopilotThread, useCopilotThreads, useCreateCopilotThread, useSendCopilotMessage } from "./api"
import { ActionCard } from "./ActionCard"
import { useAgents } from "./team-api"
import { teamReason } from "./team-text"
import { AgentMention } from "./AgentMention"
import { aiErrorText, useAiAvailability } from "./text"

const MAX_LENGTH = 4000

export function AssistantPanel({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const { status, enabled } = useAiAvailability()
  const agents = useAgents(open)
  const manager = agents.data?.items.find((agent) => agent.key === "operations_manager")
  const [agentKey, setAgentKey] = useState<string | undefined>()
  const threads = useCopilotThreads(open)
  const [threadId, setThreadId] = useState<string | undefined>(undefined)
  const thread = useCopilotThread(threadId)
  const create = useCreateCopilotThread()
  const { stream, send, retry, abort, reset } = useSendCopilotMessage(threadId)
  const [text, setText] = useState("")
  const [startError, setStartError] = useState<string | null>(null)
  const log = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const busy = stream.status === "streaming" || create.isPending
  // Unknown status (still loading) does not block typing; a known "off" does.
  const managerOff = manager !== undefined && !manager.enabled
  const targetOff = !!agentKey && !agents.data?.items.some((agent) => agent.key === agentKey && agent.enabled)
  const blocked = (status !== undefined && !enabled) || managerOff || targetOff

  // Keep the newest line in view while steps arrive.
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight
  }, [stream.steps.length, stream.answer, thread.data?.messages.length])

  const pick = (id: string | undefined) => {
    reset()
    setStartError(null)
    setAgentKey(undefined)
    setThreadId(id)
  }

  const submit = async (event?: FormEvent) => {
    event?.preventDefault()
    const content = text.trim()
    if (!content || busy || blocked) return
    setStartError(null)
    setText("")
    try {
      let id = threadId
      if (!id) {
        const created = await create.mutateAsync({ title: content.slice(0, 60) })
        id = created.id
        setThreadId(id)
      }
      await send(content, id, agentKey)
    } catch (error) {
      setText(content)
      setStartError(aiErrorText(t, problemCode(error)))
    }
  }
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit() }
  }

  const messages = thread.data?.messages ?? []
  const loadingThread = threadId !== undefined && thread.isPending
  const live = stream.status === "streaming" || stream.status === "error"

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full max-w-none gap-0 sm:w-[30rem] sm:max-w-[30rem]"
        onOpenAutoFocus={(event) => { event.preventDefault(); input.current?.focus() }}>
        <SheetHeader className="pr-12">
          <SheetTitle className="flex items-center gap-2"><Sparkles aria-hidden="true" className="size-4" /> {t("ai.assistant.title")}</SheetTitle>
          <SheetDescription>{t("ai.assistant.description")}</SheetDescription>
        </SheetHeader>
        <div className="space-y-3 px-4 pb-2">
          <SyntheticDemoBanner />
          <AiDisabledBanner />
          <nav aria-label={t("ai.assistant.threads")} className="space-y-1">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-xs font-medium uppercase text-muted-foreground">{t("ai.assistant.threads")}</h3>
              <Button type="button" size="sm" variant="outline" disabled={stream.status === "streaming"} onClick={() => pick(undefined)}>{t("ai.assistant.newChat")}</Button>
            </div>
            {threads.error ? <QueryError error={threads.error} onRetry={() => threads.refetch()} /> : threads.isPending ? <Skeleton className="h-8 w-full" /> : !threads.data?.length ? (
              <p className="text-sm text-muted-foreground">{t("ai.assistant.noThreads")}</p>
            ) : (
              <ul className="max-h-28 space-y-0.5 overflow-y-auto">
                {threads.data.map((th) => (
                  <li key={th.id}>
                    <button type="button" aria-current={th.id === threadId ? "true" : undefined} disabled={stream.status === "streaming"} onClick={() => pick(th.id)}
                      className={cn("min-h-9 w-full truncate rounded-md px-2 py-1 text-left text-sm hover:bg-accent", th.id === threadId && "bg-accent font-medium")}>
                      {th.title || t("ai.assistant.untitled")}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </nav>
        </div>

        <div ref={log} role="log" aria-live="polite" aria-label={t("ai.assistant.title")} className="min-h-0 flex-1 space-y-3 overflow-y-auto border-y px-4 py-3">
          {threadId && thread.error ? <QueryError error={thread.error} onRetry={() => thread.refetch()} /> : null}
          {loadingThread ? <p className="text-sm text-muted-foreground">{t("ai.assistant.loadingChat")}</p> : null}
          {!messages.length && !live && !loadingThread ? <p className="text-sm text-muted-foreground">{t("ai.assistant.empty")}</p> : null}
          {messages.map((m) => (
            <Bubble key={m.id} who={m.role === "user" ? t("ai.assistant.you") : t("ai.assistant.assistant")} mine={m.role === "user"} text={m.content}>
              {m.proposals?.length ? <div aria-label={t("ai.assistant.proposals")}>{m.proposals.map((card) => <ActionCard key={`${card.id}:${card.revision_id}`} card={card} threadId={threadId!} onReload={() => void thread.refetch()} />)}</div> : m.proposal_ids.length ? (
                <ul aria-label={t("ai.assistant.proposals")} className="mt-2 flex flex-wrap gap-2">
                  {m.proposal_ids.map((id, i) => <li key={id}><ProposalLink id={id} label={t("ai.assistant.proposalN", { n: i + 1 })} /></li>)}
                </ul>
              ) : null}
            </Bubble>
          ))}
          {live && stream.question ? <Bubble who={t("ai.assistant.you")} mine text={stream.question} /> : null}
          {live ? (
            <div className="space-y-2" data-testid="live-answer">
              {stream.steps.length ? (
                <ol aria-label={t("ai.assistant.steps")} className="space-y-1 text-sm text-muted-foreground">
                  {stream.steps.map((s) => <li key={s.key}>{s.text ?? t("ai.assistant.working")}</li>)}
                </ol>
              ) : null}
              {stream.status === "streaming" && stream.answer === null ? <p className="text-sm text-muted-foreground">{t("ai.assistant.working")}</p> : null}
              {stream.proposals.length ? (
                <ul aria-label={t("ai.assistant.proposals")} className="flex flex-wrap gap-2">
                  {stream.proposals.map((p) => <li key={p.id}><ProposalLink id={p.id} label={p.title || t("ai.assistant.proposalN", { n: 1 })} /></li>)}
                </ul>
              ) : null}
              {stream.answer !== null ? <Bubble who={t("ai.assistant.assistant")} text={stream.answer} /> : null}
              {stream.status === "error" ? <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">{aiErrorText(t, stream.errorCode)}</p> : null}
            </div>
          ) : null}
          {startError ? <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">{startError}</p> : null}
        </div>

        {managerOff ? <p role="status" className="p-4 text-sm">{manager.reason ? teamReason(t, manager.reason) : t("ai.team.managerOff")}</p> : <form className="space-y-2 px-4 pt-1" onSubmit={(e) => void submit(e)}>
          <p className="text-xs text-muted-foreground">{t("ai.cards.confirmHint")}</p>
          {agents.error ? <QueryError error={agents.error} onRetry={() => agents.refetch()} /> : null}
          <AgentMention agents={agents.data?.items ?? []} selected={agentKey} onSelect={setAgentKey} text={text} onTextChange={setText} disabled={busy || blocked} />
          {targetOff ? <p role="alert">{t("ai.team.targetOff")}</p> : null}
          <Label htmlFor="assistant-input">{t("ai.assistant.inputLabel")}</Label>
          <Textarea dictation={{ context: "assistant" }} id="assistant-input" ref={input} value={text} maxLength={MAX_LENGTH} disabled={blocked} rows={2} placeholder={t("ai.assistant.placeholder")}
            onChange={(e) => setText(e.target.value)} onKeyDown={onKeyDown} />
          <div className="flex justify-end gap-2">
            {stream.status === "error" ? <Button type="button" variant="outline" disabled={busy || blocked} onClick={() => void retry()}>{t("ai.assistant.retry")}</Button> : null}
            {stream.status === "streaming" ? <Button type="button" variant="outline" onClick={abort}>{t("ai.assistant.stop")}</Button> : null}
            <Button type="submit" disabled={busy || blocked || !text.trim()}>{t("ai.assistant.send")}</Button>
          </div>
        </form>}
        <p className="px-4 pb-4 text-xs text-muted-foreground">{t("ai.mistakes")}</p>
      </SheetContent>
    </Sheet>
  )
}

function Bubble({ who, text, mine, children }: { who: string; text: string; mine?: boolean; children?: ReactNode }) {
  return (
    <div className={cn("max-w-[90%] rounded-lg px-3 py-2 text-sm", mine ? "ml-auto bg-primary text-primary-foreground" : "bg-muted")}>
      <span className="block text-xs font-medium opacity-80">{who}</span>
      <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{text}</p>
      {children}
    </div>
  )
}

function ProposalLink({ id, label }: { id: string; label: string }) {
  return (
    <Link to="/approvals/$proposalId" params={{ proposalId: id }}
      className="inline-flex min-h-9 items-center gap-1 rounded-md border bg-background px-3 py-2 text-sm underline-offset-2 hover:bg-accent hover:underline">
      <Sparkles aria-hidden="true" className="size-3" /> {label}
    </Link>
  )
}
