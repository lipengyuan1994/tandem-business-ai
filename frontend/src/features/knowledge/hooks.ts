import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { Schemas } from "@/api/types"

export type Article = Schemas["ArticleDetail"]
export type Suggestion = Schemas["SuggestionOut"]
export type ArticleInput = Schemas["ArticleCreate"]
export type Filters = { query?: string; status?: "draft" | "published" | "archived"; visibility?: "public" | "internal" }
export const knowledgeKeys = { all: ["knowledge"] as const, article: (id: string) => ["knowledge", "article", id] as const }
export function useArticles(filters: Filters) {
  return useQuery({ queryKey: ["knowledge", "articles", filters], queryFn: async () => unwrap(await api.GET("/api/v1/knowledge/articles", { params: { query: { ...filters, limit: 100 } } })) })
}
export function useArticle(id?: string) {
  return useQuery({ queryKey: knowledgeKeys.article(id ?? ""), enabled: !!id, queryFn: async () => unwrap(await api.GET("/api/v1/knowledge/articles/{article_id}", { params: { path: { article_id: id! } } })) })
}
export function useSuggestions() {
  return useQuery({ queryKey: ["knowledge", "suggestions"], queryFn: async () => unwrap(await api.GET("/api/v1/knowledge/suggestions", { params: { query: { status: "open", limit: 100 } } })) })
}
export function useSaveArticle(article?: Article, suggestion?: Suggestion) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: async (body: ArticleInput) => {
    if (article) return unwrap(await api.PATCH("/api/v1/knowledge/articles/{article_id}", { params: { path: { article_id: article.id } }, body: { title: body.title, body_markdown: body.body_markdown, tags: body.tags, visibility: body.visibility, version: article.version } }))
    if (suggestion) return unwrap(await api.POST("/api/v1/knowledge/suggestions/{suggestion_id}/convert", { params: { path: { suggestion_id: suggestion.id } }, body: { article: body, version: suggestion.version } }))
    return unwrap(await api.POST("/api/v1/knowledge/articles", { body }))
  }, onSuccess: (saved) => { qc.setQueryData(knowledgeKeys.article(saved.id), saved); void qc.invalidateQueries({ queryKey: knowledgeKeys.all }) } })
}
export function useArticleStatus(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: async ({ action, version }: { action: "publish" | "archive"; version: number }) => {
    const params = { path: { article_id: id } }
    return action === "publish" ? unwrap(await api.POST("/api/v1/knowledge/articles/{article_id}/publish", { params, body: { version } })) : unwrap(await api.POST("/api/v1/knowledge/articles/{article_id}/archive", { params, body: { version } }))
  }, onSuccess: (saved) => { qc.setQueryData(knowledgeKeys.article(id), saved); void qc.invalidateQueries({ queryKey: knowledgeKeys.all }) } })
}
export function useDismissSuggestion() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: async (suggestion: Suggestion) => unwrap(await api.POST("/api/v1/knowledge/suggestions/{suggestion_id}/dismiss", { params: { path: { suggestion_id: suggestion.id } }, body: { version: suggestion.version } })), onSuccess: () => qc.invalidateQueries({ queryKey: knowledgeKeys.all }) })
}
