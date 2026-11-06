import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"
export const knowledgeRoute = createRoute({ getParentRoute: () => appRoute, path: "/knowledge", component: lazyRouteComponent(() => import("./KnowledgePage"), "KnowledgePage") })
export const knowledgeNewRoute = createRoute({ getParentRoute: () => appRoute, path: "/knowledge/new", component: lazyRouteComponent(() => import("./KnowledgePage"), "KnowledgeArticlePage") })
export const knowledgeArticleRoute = createRoute({ getParentRoute: () => appRoute, path: "/knowledge/$articleId", validateSearch: (s: Record<string, unknown>): { version?: number } => ({ version: Number.isInteger(Number(s.version)) && Number(s.version) > 0 ? Number(s.version) : undefined }), component: lazyRouteComponent(() => import("./KnowledgePage"), "KnowledgeArticlePage") })
