import { OwnershipControls } from "./OwnershipControls"
import { ReplyPublication } from "./ReplyPublication"
import { QueryError } from "@/components/common/QueryError"
import { Link, useNavigate, useParams } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { Thread } from "@/api/types"
import { useSession } from "@/app/session-context"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { SummariseButton } from "@/features/ai/SummariseButton"
import { useCustomerMatch } from "@/features/crm/hooks"
import { toastError } from "@/lib/errors"
import { cn } from "@/lib/utils"
import { type InboxTab, useConversations, useSendMessage, useThread, useUpdateConversation } from "./hooks"

/** Three panes: thread list (status tabs) · thread with composer · customer card (M04 UI). */
export function InboxPage() {
  const { t } = useTranslation()
  const params = useParams({ strict: false }) as { conversationId?: string }
  const navigate = useNavigate()
  const [tab, setTab] = useState<InboxTab>("waiting_on_staff")
  const list = useConversations(tab)
  const thread = useThread(params.conversationId)

  return (
    <>
      <PageHeader title={t("nav.inbox")} description={t("inbox.description")} />
      <div className="grid min-h-[32rem] min-w-0 gap-4 lg:grid-cols-[18rem_minmax(0,1fr)] xl:grid-cols-[18rem_minmax(0,1fr)_16rem] [&>*]:min-w-0">
        <nav aria-label={t("inbox.threads")} className={cn("min-w-0 space-y-2", params.conversationId && "hidden lg:block")}>
          <Tabs value={tab} onValueChange={(v) => setTab(v as InboxTab)}>
            <TabsList className="w-full">
              {(["waiting_on_staff", "waiting_on_customer", "closed"] as const).map((s) => <TabsTrigger key={s} value={s}>{t(`inbox.tabs.${s}`)}</TabsTrigger>)}
            </TabsList>
            <TabsContent value={tab}>
          {list.error ? <QueryError error={list.error} onRetry={() => list.refetch()} /> : list.isPending ? <Skeleton className="h-40 w-full" /> : !list.data?.items.length ? <p className="p-3 text-sm text-muted-foreground">{t("inbox.empty")}</p> : (
            <ul className="divide-y rounded-lg border">
              {list.data.items.map((c) => (
                <li key={c.id}>
                  <Link to="/inbox/$conversationId" params={{ conversationId: c.id }}
                    className={cn("block px-3 py-2 hover:bg-accent", c.id === params.conversationId && "bg-accent")}
                    aria-current={c.id === params.conversationId ? "page" : undefined}>
                    <span className="flex items-center justify-between gap-2 text-sm font-medium">
                      {c.customer_name ?? c.visitor_name ?? t("inbox.anonymous")}
                      <Badge variant="outline">{t(`inbox.channels.${c.channel}`)}</Badge>
                    </span>
                    <span className="block text-xs">{t(c.ai_mode === "human" ? "inbox.ai.human" : "inbox.ai.auto")}</span>
                    {c.handoff_requested_at ? <Badge variant="secondary">{t("inbox.ai.handoff")}</Badge> : null}
                    <span className="line-clamp-2 break-words text-sm text-muted-foreground">{c.last_message_preview}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
            </TabsContent>
          </Tabs>
        </nav>
        <section aria-label={t("inbox.thread")} className="flex min-w-0 flex-col rounded-lg border">
          {params.conversationId ? <Link to="/inbox" className="record-link px-4 lg:hidden">{t("inbox.back")}</Link> : null}
          {!params.conversationId ? <div className="flex flex-1 items-center justify-center p-6"><EmptyState title={t("inbox.pick")} description={t("inbox.pickHint")} /></div>
            : thread.error ? <QueryError error={thread.error} onRetry={() => thread.refetch()} notFound={t("inbox.notFound")} />
            : thread.isPending ? <Skeleton className="h-full w-full" />
            : thread.data ? <ThreadView key={thread.data.id} thread={thread.data} onClosed={() => void navigate({ to: "/inbox" })} /> : <EmptyState title={t("inbox.notFound")} />}
        </section>
        <aside aria-label={t("inbox.customer")} className="lg:col-start-2 xl:col-auto">{thread.data ? <CustomerCard thread={thread.data} onReload={() => void thread.refetch()} /> : null}</aside>
      </div>
    </>
  )
}

function ThreadView({ thread, onClosed }: { thread: Thread; onClosed: () => void }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const send = useSendMessage(thread.id)
  const update = useUpdateConversation(thread.id)
  const [body, setBody] = useState("")
  const [internal, setInternal] = useState(false)
  const submit = () => body.trim() && send.mutate({ direction: internal ? "internal" : "outbound", body: body.trim() }, { onSuccess: () => setBody(""), onError: (e) => toastError(e) })

  return (
    <>
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2">
        <h2 className="font-medium">{thread.subject ?? thread.customer_name ?? thread.visitor_name ?? t("inbox.anonymous")}</h2>
        <span className="flex flex-wrap items-center gap-2">
          <StatusBadge status={thread.status} />
          <SummariseButton conversationId={thread.id} />
          {thread.status !== "closed" ? (
            <PermissionGate permission="conversations.thread.write">
              <Button size="sm" variant="outline" onClick={() => update.mutate({ version: thread.version, status: "closed" }, { onSuccess: onClosed, onError: (e) => toastError(e) })}>{t("inbox.close")}</Button>
            </PermissionGate>
          ) : null}
        </span>
      </header>
      <OwnershipControls thread={thread} />
      {thread.contact_consent_at ? <p className="px-4 pt-3 text-xs text-muted-foreground">
        {t("inbox.contactConsent")} ({<LocalTime iso={thread.contact_consent_at} timeZone={businessTimeZone} pattern="d MMM yyyy" />})
      </p> : null}
      <ol className="flex-1 space-y-3 overflow-y-auto p-4" aria-live="polite">
        {thread.messages.map((m) => (
          <li key={m.id} className={cn("max-w-[80%] rounded-lg px-3 py-2 text-sm", m.direction === "inbound" && "bg-muted",
            m.direction === "outbound" && (m.publication_state === "withheld" || m.publication_state === "cancelled" || m.publication_state === "stopped" || m.author_kind === "system" ? "ml-auto border border-dashed bg-muted" : "ml-auto bg-primary text-primary-foreground"), m.direction === "internal" && "mx-auto border border-dashed border-status-pending bg-status-pending/10")}
            data-direction={m.direction}>
            {m.direction === "internal" ? <span className="block text-xs font-medium">{t("inbox.internalNote")}</span> : null}
            <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{m.body}</p>
            <ReplyPublication message={m} />
            <span className="mt-1 block text-xs opacity-75">
              <LocalTime iso={m.created_at} timeZone={businessTimeZone} pattern="d MMM HH:mm" />
              {m.delivery_status ? ` · ${t(`status.${m.delivery_status}`, { defaultValue: m.delivery_status })}` : ""}
            </span>
          </li>
        ))}
      </ol>
      <PermissionGate permission="conversations.thread.write">
        <form className="space-y-2 border-t p-3" onSubmit={(e) => { e.preventDefault(); submit() }}>
          <Label htmlFor="composer">{internal ? t("inbox.noteLabel") : t("inbox.replyLabel")}</Label>
          <Textarea id="composer" value={body} onChange={(e) => setBody(e.target.value)} placeholder={internal ? t("inbox.notePlaceholder") : t("inbox.replyPlaceholder")}
            className={cn(internal && "border-dashed border-status-pending")} />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="flex items-center gap-2">
              <Checkbox id="internal-toggle" checked={internal} onCheckedChange={(v) => setInternal(v === true)} />
              <Label htmlFor="internal-toggle">{t("inbox.internalToggle")}</Label>
            </span>
            <Button type="submit" disabled={send.isPending || !body.trim()}>{internal ? t("inbox.addNote") : t("inbox.send")}</Button>
          </div>
        </form>
      </PermissionGate>
    </>
  )
}

function CustomerCard({ thread, onReload }: { thread: Thread; onReload: () => void }) {
  const { t } = useTranslation()
  const update = useUpdateConversation(thread.id)
  const [query, setQuery] = useState(thread.visitor_name ?? "")
  const [conflict, setConflict] = useState(false)
  const matches = useCustomerMatch(thread.customer_id ? "" : query)

  if (thread.customer_id) {
    return (
      <div className="space-y-2 rounded-lg border p-3 text-sm">
        <p className="font-medium">{thread.customer_name}</p>
        <Link className="underline" to="/customers/$customerId" params={{ customerId: thread.customer_id }}>{t("inbox.openProfile")}</Link>
      </div>
    )
  }
  return (
    <div className="space-y-2 rounded-lg border p-3 text-sm">
      <p className="font-medium">{thread.visitor_name ?? t("inbox.anonymous")}</p>
      {thread.visitor_email ? <p className="text-muted-foreground">{thread.visitor_email}</p> : null}
      <PermissionGate permission="conversations.thread.write">
        <Label htmlFor="link-customer">{t("inbox.linkCustomer")}</Label>
        <Input id="link-customer" value={query} onChange={(e) => setQuery(e.target.value)} autoComplete="off" />
        {matches.error ? <QueryError error={matches.error} onRetry={() => matches.refetch()} /> : matches.data?.length ? (
          <ul className="space-y-1">
            {matches.data.slice(0, 5).map((m) => (
              <li key={m.customer_id}>
                <Button size="sm" variant="outline" className="w-full justify-start" disabled={update.isPending}
                  onClick={() => update.mutate({ version: thread.version, customer_id: m.customer_id }, { onError: (e) => toastError(e, () => setConflict(true)) })}>
                  {t("inbox.linkTo", { name: m.display_name })}
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
      </PermissionGate>
      <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); onReload() }} />
    </div>
  )
}
