// Hooks of docs/roadmap/ai/contracts.md §16. Query keys: ["ai", resource, id | params].
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"
import { api, unwrap } from "@/api/client"
import { useSession } from "@/app/session-context"
import { ApiProblem } from "@/lib/problem"
import { aiHeaders, aiRequest, aiUrl, problemFrom } from "./http"
import { applyStepEvent, IDLE_STREAM, readStepEvents, type CopilotStream } from "./stream"
import type {
  CampaignDraftOut, ExtractIn, ExtractOut, MessageCreate, RequestQuotesOut, ThreadCreate,
} from "./types"

export const ASSISTANT_USE = "ai.assistant.use"
export const SETTINGS_READ = "ai.settings.read"

export const aiKeys = {
  all: ["ai"] as const,
  status: ["ai", "status"] as const,
  threads: ["ai", "threads"] as const,
  thread: (id: string) => ["ai", "thread", id] as const,
  briefing: ["ai", "briefing"] as const,
  runs: ["ai", "runs"] as const,
  usage: ["ai", "usage"] as const,
}

/** Whether AI is available for this business. Not fetched for people without `ai.assistant.use`. */
export function useAiStatus() {
  const { permissions } = useSession()
  return useQuery({
    queryKey: aiKeys.status,
    enabled: permissions.has(ASSISTANT_USE),
    queryFn: async () => unwrap(await api.GET("/api/v1/ai/status")),
  })
}

export function useCopilotThreads(enabled = true) {
  return useQuery({ queryKey: aiKeys.threads, enabled, queryFn: async () => unwrap(await api.GET("/api/v1/ai/copilot/threads")) })
}

export function useCopilotThread(id: string | undefined) {
  return useQuery({
    queryKey: aiKeys.thread(id ?? ""),
    enabled: !!id,
    queryFn: async () => unwrap(await api.GET("/api/v1/ai/copilot/threads/{id}", { params: { path: { id: id! } } })),
  })
}

export function useCreateCopilotThread() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: ThreadCreate) => unwrap(await api.POST("/api/v1/ai/copilot/threads", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: aiKeys.threads }),
  })
}

/** Things an AI answer can change: proposals waiting for approval, the counters, thread lists. */
function refreshAfterRun(qc: QueryClient) {
  return Promise.all([qc.invalidateQueries({ queryKey: ["approvals"] }), qc.invalidateQueries({ queryKey: ["dashboard"] })])
}

/** Map the platform copilot rate limit to the AI screen copy; preserve other problem codes. */
export function problemCode(error: unknown): string {
  if (error instanceof ApiProblem) return error.status === 429 && error.code === "platform.rate_limited" ? "ai.rate_limited" : error.code
  return "ai.interrupted"
}

/**
 * Sends one question and streams the answer (SSE over `fetch`, so the CSRF header can be sent).
 * The request is aborted when the component unmounts or when `abort`/`send` is called again.
 */
export function useSendCopilotMessage(threadId: string | undefined) {
  const qc = useQueryClient()
  const [stream, setStream] = useState<CopilotStream>(IDLE_STREAM)
  const controller = useRef<AbortController | null>(null)

  useEffect(() => () => controller.current?.abort(), [])

  const reset = useCallback(() => {
    controller.current?.abort()
    controller.current = null
    setStream(IDLE_STREAM)
  }, [])

  const send = useCallback(async (content: string, threadOverride?: string) => {
    const id = threadOverride ?? threadId
    if (!id) return
    controller.current?.abort()
    const mine = new AbortController()
    controller.current = mine
    const current = () => controller.current === mine
    setStream({ ...IDLE_STREAM, status: "streaming", question: content })
    let finished = false
    const headers = aiHeaders("POST", true)
    headers.set("Accept", "text/event-stream")
    try {
      const response = await globalThis.fetch(aiUrl(`/copilot/threads/${id}/messages`), {
        method: "POST",
        credentials: "same-origin",
        signal: mine.signal,
        headers,
        body: JSON.stringify({ content } satisfies MessageCreate),
      })
      if (!response.ok) throw await problemFrom(response)
      await readStepEvents(response, (event) => {
        if (!current()) return
        if (event.kind === "final" || event.kind === "error") finished = true
        setStream((s) => applyStepEvent(s, event))
      })
      if (!finished && current()) setStream((s) => ({ ...s, status: "error", errorCode: "ai.interrupted" }))
    } catch (error) {
      if (mine.signal.aborted || !current()) return
      setStream((s) => ({ ...s, status: "error", errorCode: problemCode(error) }))
    }
    if (!current()) return
    // Show the saved conversation (which now holds the question, answer and proposals), then drop the live block.
    await Promise.all([qc.invalidateQueries({ queryKey: aiKeys.thread(id) }), qc.invalidateQueries({ queryKey: aiKeys.threads }), refreshAfterRun(qc)]).catch(() => undefined)
    if (current()) setStream((s) => (s.status === "error" ? s : { ...s, status: "done" }))
  }, [qc, threadId])

  const abort = useCallback(() => {
    controller.current?.abort()
    setStream((s) => (s.status === "streaming" ? { ...s, status: "done" } : s))
    void qc.invalidateQueries({ queryKey: aiKeys.threads })
    if (threadId) void qc.invalidateQueries({ queryKey: aiKeys.thread(threadId) })
  }, [qc, threadId])

  return { stream, send, abort, reset }
}

/** One-click summary of an inbox conversation; also drafts a reply proposal. */
export function useThreadSummary(conversationId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/ai/conversations/{id}/summary", { params: { path: { id: conversationId } } })),
    onSuccess: () => Promise.all([refreshAfterRun(qc), qc.invalidateQueries({ queryKey: aiKeys.usage })]),
  })
}

export function useBriefing(enabled: boolean) {
  return useQuery({ queryKey: aiKeys.briefing, enabled, staleTime: 5 * 60_000, queryFn: async () => unwrap(await api.GET("/api/v1/ai/briefing")) })
}

/** Campaign draft (AI-15). No screen uses this yet; it is here because §16 lists it. */
export function useCampaignDraft(campaignId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { purpose?: string }) => aiRequest<CampaignDraftOut>("POST", `/campaigns/${campaignId}/draft`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["marketing"] }),
  })
}

export function useExtractSupplierQuote(requestId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ExtractIn) => aiRequest<ExtractOut>("POST", `/procurement/quote-requests/${requestId}/extract`, body),
    onSuccess: () => Promise.all([refreshAfterRun(qc), qc.invalidateQueries({ queryKey: ["purchasing"] })]),
  })
}

export function useRequestQuotes(itemId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => aiRequest<RequestQuotesOut>("POST", `/procurement/items/${itemId}/request-quotes`),
    onSuccess: () => Promise.all([refreshAfterRun(qc), qc.invalidateQueries({ queryKey: ["purchasing"] })]),
  })
}

export function useAiRuns(enabled = true) {
  return useInfiniteQuery({
    queryKey: aiKeys.runs,
    enabled,
    initialPageParam: "",
    queryFn: async ({ pageParam }) => unwrap(await api.GET("/api/v1/ai/runs", { params: { query: { cursor: pageParam || undefined } } })),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}

export function useAiUsage(enabled = true) {
  return useQuery({ queryKey: aiKeys.usage, enabled, queryFn: async () => unwrap(await api.GET("/api/v1/ai/usage")) })
}
