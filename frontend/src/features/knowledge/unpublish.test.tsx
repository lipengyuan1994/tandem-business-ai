import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import type { Schemas } from "@/api/types"

const ARTICLE: Schemas["ArticleDetail"] = {
  source: "manual", search_index_state: "not_applicable",
  id: "00000000-0000-7000-8000-0000000000d1", title: "Returns", slug: "returns", status: "draft", visibility: "public",
  current_version: 2, published_version: null, version: 3, created_by: null,
  created_at: "2026-11-05T17:00:00Z", updated_at: "2026-11-05T17:00:00Z",
  versions: [1, 2].map((n) => ({ version_no: n, title: "Returns", body_markdown: "Bring your receipt.", tags: ["policy"], author: null, published_at: null, created_at: "2026-11-05T17:00:00Z" })),
}

function lists() {
  server.use(
    http.get("*/api/v1/knowledge/articles", () => HttpResponse.json([])),
    http.get("*/api/v1/knowledge/suggestions", () => HttpResponse.json([])),
  )
}

it("takes a published article back to draft after confirming", async () => {
  mockMe(meWith("conversations.thread.read", "org.settings.write"))
  lists()
  let article = { ...ARTICLE, status: "published", published_version: 2, version: 5 }
  let sent: unknown = null
  server.use(
    http.get("*/api/v1/knowledge/articles/:id", () => HttpResponse.json(article)),
    http.post("*/api/v1/knowledge/articles/:id/unpublish", async ({ request }) => { sent = await request.json(); article = { ...article, status: "draft", version: 6 }; return HttpResponse.json(article) }),
  )
  renderApp(`/knowledge/${ARTICLE.id}`)
  await userEvent.click(await screen.findByRole("button", { name: "Unpublish" }))
  await userEvent.click(within(await screen.findByRole("dialog", { name: "Unpublish article" })).getByRole("button", { name: "Unpublish article" }))
  await waitFor(() => expect(sent).toEqual({ version: 5 }))
  await waitFor(() => expect(screen.queryByRole("button", { name: "Unpublish" })).not.toBeInTheDocument())
})

it("offers unpublish only on published articles", async () => {
  mockMe(meWith("conversations.thread.read", "org.settings.write"))
  lists()
  server.use(http.get("*/api/v1/knowledge/articles/:id", () => HttpResponse.json(ARTICLE)))
  renderApp(`/knowledge/${ARTICLE.id}`)
  expect(await screen.findByRole("button", { name: "Publish" })).toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "Unpublish" })).not.toBeInTheDocument()
})
