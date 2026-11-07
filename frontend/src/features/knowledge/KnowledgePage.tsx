import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { EmptyState } from "@/components/common/EmptyState"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { toastError } from "@/lib/errors"
import { ArticleEditor } from "./ArticleEditor"
import { PdfImport } from "./PdfImport"
import { type Filters, type Suggestion, useArticle, useArticles, useDismissSuggestion, useSuggestions } from "./hooks"

export function KnowledgePage() {
  const { t } = useTranslation()
  return <PermissionGate permission="conversations.thread.read" fallback={<EmptyState title={t("knowledge.noAccess")} />}><KnowledgeList /></PermissionGate>
}
function KnowledgeList() {
  const { t } = useTranslation()
  const [filters, setFilters] = useState<Filters>({})
  const [tab, setTab] = useState("articles")
  const articles = useArticles(filters)
  return <div className="space-y-4">
    <PageHeader title={t("nav.knowledge")} description={t("knowledge.description")} actions={<PermissionGate permission="org.settings.write"><Link className="record-link" to="/knowledge/new">{t("knowledge.new")}</Link></PermissionGate>} />
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList><TabsTrigger value="articles">{t("knowledge.articles")}</TabsTrigger><TabsTrigger value="suggestions">{t("knowledge.suggestions")}</TabsTrigger></TabsList>
      <TabsContent value="articles" className="space-y-4">
        <PermissionGate permission="org.settings.write"><PdfImport /></PermissionGate>
        <div className="flex flex-wrap gap-3">
          <Input type="search" className="max-w-md" aria-label={t("knowledge.search")} value={filters.query ?? ""} onChange={(e) => setFilters({ ...filters, query: e.target.value || undefined })} />
          <label className="space-y-1 text-sm">{t("knowledge.statusFilter")}<select className="block min-h-11 rounded-md border bg-background p-2" value={filters.status ?? ""} onChange={(e) => setFilters({ ...filters, status: e.target.value as Filters["status"] || undefined })}><option value="">{t("knowledge.allStatuses")}</option>{["draft", "published", "archived"].map((s) => <option key={s} value={s}>{t(`knowledge.statuses.${s}`)}</option>)}</select></label>
          <label className="space-y-1 text-sm">{t("knowledge.visibilityFilter")}<select className="block min-h-11 rounded-md border bg-background p-2" value={filters.visibility ?? ""} onChange={(e) => setFilters({ ...filters, visibility: e.target.value as Filters["visibility"] || undefined })}><option value="">{t("knowledge.allVisibilities")}</option>{["public", "internal"].map((s) => <option key={s} value={s}>{t(`knowledge.visibilities.${s}`)}</option>)}</select></label>
        </div>
        {articles.error ? <QueryError error={articles.error} onRetry={() => articles.refetch()} /> : articles.isPending ? <p role="status">{t("common.loading")}</p> : !articles.data?.length ? <EmptyState title={t("knowledge.empty")} /> : <ul className="divide-y rounded-md border">{articles.data.map((article) => <li key={article.id} className="flex flex-wrap items-center gap-3 p-3"><Link to="/knowledge/$articleId" params={{ articleId: article.id }} className="record-link mr-auto">{article.title}</Link><Badge variant="outline">{t(`knowledge.statuses.${article.status}`)}</Badge><Badge variant="secondary">{t(`knowledge.visibilities.${article.visibility}`)}</Badge>{article.status === "published" && article.visibility === "public" && <Badge variant="outline">{t(`knowledge.index.${article.search_index_state ?? "pending"}`)}</Badge>}</li>)}</ul>}
        <p className="text-sm text-muted-foreground">{t("knowledge.listLimit")}</p>
      </TabsContent>
      <TabsContent value="suggestions"><Suggestions /></TabsContent>
    </Tabs>
  </div>
}
function Suggestions() {
  const { t } = useTranslation()
  const suggestions = useSuggestions()
  const dismiss = useDismissSuggestion()
  const navigate = useNavigate()
  const [writing, setWriting] = useState<Suggestion>()
  if (writing) return <div className="space-y-3"><Button variant="outline" onClick={() => setWriting(undefined)}>{t("common.cancel")}</Button><ArticleEditor suggestion={writing} onReload={() => { setWriting(undefined); void suggestions.refetch() }} onSaved={(a) => void navigate({ to: "/knowledge/$articleId", params: { articleId: a.id } })} /></div>
  if (suggestions.error) return <QueryError error={suggestions.error} onRetry={() => suggestions.refetch()} />
  return <div className="space-y-3">
    <p className="text-sm text-muted-foreground">{t("knowledge.suggestionsHelp")}</p>
    {suggestions.isPending ? <p role="status">{t("common.loading")}</p> : !suggestions.data?.length ? <EmptyState title={t("knowledge.noSuggestions")} /> : <ul className="space-y-3">{suggestions.data.map((s) => <li key={s.id} className="space-y-2 rounded-md border p-3"><p>{s.question_text}</p><PermissionGate permission="org.settings.write"><div className="flex gap-2"><Button size="sm" onClick={() => setWriting(s)}>{t("knowledge.writeArticle")}</Button><Button size="sm" variant="outline" disabled={dismiss.isPending} onClick={() => dismiss.mutate(s, { onError: (e) => { toastError(e); void suggestions.refetch() } })}>{t("knowledge.dismiss")}</Button></div></PermissionGate></li>)}</ul>}
  </div>
}
export function KnowledgeArticlePage() {
  const { t } = useTranslation()
  return <PermissionGate permission="conversations.thread.read" fallback={<EmptyState title={t("knowledge.noAccess")} />}><ArticlePage /></PermissionGate>
}
function ArticlePage() {
  const { t } = useTranslation()
  const { articleId } = useParams({ strict: false }) as { articleId?: string }
  const { version } = useSearch({ strict: false }) as { version?: number }
  const article = useArticle(articleId)
  const navigate = useNavigate()
  const [reload, setReload] = useState(0)
  if (articleId && article.error) return <QueryError error={article.error} onRetry={() => article.refetch()} />
  if (articleId && !article.data) return <p role="status">{t("common.loading")}</p>
  return <div className="max-w-4xl space-y-5"><Link className="record-link" to="/knowledge">{t("nav.knowledge")}</Link><PageHeader title={article.data?.title ?? t("knowledge.new")} />
    <PermissionGate permission={articleId ? "conversations.thread.read" : "org.settings.write"} fallback={<EmptyState title={t("knowledge.readOnly")} />}><ArticleEditor key={`${articleId}:${article.data?.version}:${reload}`} article={article.data} citedVersion={version} onReload={() => { setReload((n) => n + 1); void article.refetch() }} onSaved={(a) => { if (!articleId) void navigate({ to: "/knowledge/$articleId", params: { articleId: a.id } }) }} /></PermissionGate>
  </div>
}
