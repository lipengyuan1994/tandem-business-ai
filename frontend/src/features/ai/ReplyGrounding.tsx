import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { Badge } from "@/components/ui/badge"
import { useProposal } from "@/features/approvals/hooks"
import { QueryError } from "@/components/common/QueryError"
import { useArticle } from "@/features/knowledge/hooks"

function object(value: unknown): Record<string, unknown> | undefined { return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined }
function citation(value: unknown): { article_id: string; version: number } | undefined {
  const c = object(value)
  return c && typeof c.article_id === "string" && /^[0-9a-f-]{36}$/i.test(c.article_id) && typeof c.version === "number" && Number.isInteger(c.version) && c.version > 0 ? { article_id: c.article_id, version: c.version } : undefined
}
function ArticleSource({ id, version }: { id: string; version: number }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  // A proposal reader may lack Knowledge permission; do not attempt that read in their session.
  return permissions.has("conversations.thread.read") ? <ReadableSource id={id} version={version} /> : <span>{t("ai.grounding.restrictedSource", { version })}</span>
}
function ReadableSource({ id, version }: { id: string; version: number }) {
  const { t } = useTranslation()
  const article = useArticle(id)
  const revision = article.data?.versions.find((v) => v.version_no === version)
  return <a className="record-link" href={`/knowledge/${id}?version=${version}`}>{t("ai.grounding.articleVersion", { title: revision?.title ?? t("ai.grounding.sourceUnavailable"), version })}</a>
}
function Source({ source }: { source: string }) {
  const { t } = useTranslation()
  const match = /^([0-9a-f-]{36})@(\d+)$/i.exec(source)
  if (match) return <ArticleSource id={match[1]!} version={Number(match[2])} />
  return <span>{source === "none" || !source ? t("ai.grounding.noSource") : source === "db_tool" ? t("ai.grounding.database") : source}</span>
}
export function ReplyGrounding({ payload }: { payload: Record<string, unknown> }) {
  const { t } = useTranslation()
  const citations = Array.isArray(payload.citations) ? payload.citations.map(citation).filter((c) => !!c) : []
  const grounding = object(payload.grounding)
  const verdict = typeof grounding?.verdict === "string" && ["supported", "unsupported", "contradicted", "unavailable"].includes(grounding.verdict) ? grounding.verdict : "unavailable"
  const claims = Array.isArray(grounding?.claims) ? grounding.claims.map(object).filter((c) => c && typeof c.text === "string" && typeof c.source === "string" && ["supported", "unsupported", "contradicted"].includes(String(c.verdict))) : []
  return <section aria-label={t("ai.grounding.title")} className="space-y-3 rounded-md border p-3">
    <h2 className="font-medium">{t("ai.grounding.title")}</h2>
    <p>{t("ai.grounding.overall")} <Badge variant={verdict === "supported" ? "secondary" : "outline"}>{t(`ai.grounding.verdicts.${verdict}`)}</Badge></p>
    <h3 className="text-sm font-medium">{t("ai.grounding.citations")}</h3>
    {citations.length ? <ul className="space-y-1">{citations.map((c) => <li key={`${c.article_id}@${c.version}`}><ArticleSource id={c.article_id} version={c.version} /></li>)}</ul> : <p className="text-sm">{t("ai.grounding.noCitations")}</p>}
    <ul className="space-y-3">{claims.map((claim, index) => <li key={index} className="space-y-1 text-sm"><p>{String(claim!.text)}</p><Badge variant="outline">{t(`ai.grounding.verdicts.${claim!.verdict}`)}</Badge><p>{t("ai.grounding.source")} <Source source={String(claim!.source)} /></p></li>)}</ul>
    {grounding && verdict === "supported" && Array.isArray(grounding.claims) && grounding.claims.length === 0 ? <p className="text-sm">{t("ai.grounding.noClaims")}</p> : null}
  </section>
}
export function CardReplyGrounding({ proposalId, revisionId }: { proposalId: string; revisionId: string }) {
  const { permissions } = useSession()
  const { t } = useTranslation()
  return permissions.has("approvals.proposal.read") ? <LoadedCardGrounding proposalId={proposalId} revisionId={revisionId} /> : <p>{t("ai.grounding.reviewForEvidence")}</p>
}
function LoadedCardGrounding({ proposalId, revisionId }: { proposalId: string; revisionId: string }) {
  const { t } = useTranslation()
  const proposal = useProposal(proposalId)
  if (proposal.error) return <QueryError error={proposal.error} onRetry={() => proposal.refetch()} />
  if (proposal.data && proposal.data.current_revision.id !== revisionId) return <p role="status">{t("ai.cards.reasons.changed")}</p>
  return proposal.data ? <ReplyGrounding payload={proposal.data.current_revision.payload} /> : null
}
