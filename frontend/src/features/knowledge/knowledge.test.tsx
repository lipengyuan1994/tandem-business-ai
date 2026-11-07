import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { Schemas } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

export const ARTICLE: Schemas["ArticleDetail"] = {
  source: "manual", search_index_state: "not_applicable",
  id: "00000000-0000-7000-8000-0000000000d1", title: "Returns", slug: "returns", status: "draft", visibility: "public",
  current_version: 2, published_version: null, version: 3, created_by: null,
  created_at: "2026-11-05T17:00:00Z", updated_at: "2026-11-05T17:00:00Z",
  versions: [1, 2].map((n) => ({ version_no: n, title: n === 1 ? "Old returns" : "Returns", body_markdown: n === 1 ? "Old policy" : "## Returns\n\nBring **your receipt**.", tags: ["policy"], author: null, published_at: null, created_at: "2026-11-05T17:00:00Z" })),
}
const suggestion: Schemas["SuggestionOut"] = { id: "s1", conversation_id: "c1", question_text: "Do you offer a warranty?", status: "open", version: 2, article_id: null, created_at: ARTICLE.created_at }
function setup(write = true) {
  mockMe(meWith("conversations.thread.read", ...(write ? ["org.settings.write"] : [])))
  const requests: { action: string; body: unknown }[] = []
  let article = structuredClone(ARTICLE)
  server.use(
    http.get("*/api/v1/knowledge/articles", ({ request }) => { const q = new URL(request.url).searchParams.get("query"); return HttpResponse.json(q && !"returns".includes(q.toLowerCase()) ? [] : [article]) }),
    http.get("*/api/v1/knowledge/articles/:id", () => HttpResponse.json(article)),
    http.post("*/api/v1/knowledge/articles", async ({ request }) => { requests.push({ action: "create", body: await request.json() }); return HttpResponse.json(article) }),
    http.patch("*/api/v1/knowledge/articles/:id", async ({ request }) => { requests.push({ action: "edit", body: await request.json() }); article = { ...article, version: 4, current_version: 3 }; return HttpResponse.json(article) }),
    http.post("*/api/v1/knowledge/articles/:id/:action", async ({ request, params }) => { requests.push({ action: String(params.action), body: await request.json() }); article = { ...article, status: params.action === "publish" ? "published" : "archived", version: 4 }; return HttpResponse.json(article) }),
    http.get("*/api/v1/knowledge/suggestions", () => HttpResponse.json([suggestion])),
    http.post("*/api/v1/knowledge/suggestions/:id/:action", async ({ request, params }) => { requests.push({ action: String(params.action), body: await request.json() }); return HttpResponse.json(params.action === "convert" ? article : { ...suggestion, status: "dismissed" }) }),
  )
  return requests
}

describe("Knowledge", () => {
  it("lists badges and searches and filters with backend query parameters", async () => {
    setup()
    const urls: URL[] = []
    server.use(http.get("*/api/v1/knowledge/articles", ({ request }) => { urls.push(new URL(request.url)); return HttpResponse.json([ARTICLE]) }))
    renderApp("/knowledge")
    const link = await screen.findByRole("link", { name: "Returns" })
    expect(link.closest("li")).toHaveTextContent("Draft")
    expect(link.closest("li")).toHaveTextContent("Public")
    await userEvent.type(screen.getByRole("searchbox", { name: "Search articles" }), "returns")
    await userEvent.selectOptions(screen.getByLabelText("Article status"), "published")
    await userEvent.selectOptions(screen.getByLabelText("Article visibility"), "internal")
    await waitFor(() => expect(urls.at(-1)?.searchParams.get("query")).toBe("returns"))
    expect(urls.at(-1)?.searchParams.get("status")).toBe("published")
    expect(urls.at(-1)?.searchParams.get("visibility")).toBe("internal")
  })

  it("creates a draft with markdown preview and saves edited articles as a new version", async () => {
    const requests = setup()
    const app = renderApp("/knowledge/new")
    await userEvent.type(await screen.findByLabelText("Title"), "Repairs")
    await userEvent.type(screen.getByLabelText("Markdown body"), "## Repair policy\n\nBring **your bike**.")
    expect(within(screen.getByRole("region", { name: "Preview" })).getByRole("heading", { name: "Repair policy" })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Tags (comma separated)"), "repair, policy")
    await userEvent.selectOptions(screen.getByLabelText("Visibility"), "internal")
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }))
    await waitFor(() => expect(requests[0]).toEqual({ action: "create", body: { title: "Repairs", slug: "repairs", body_markdown: "## Repair policy\n\nBring **your bike**.", tags: ["repair", "policy"], visibility: "internal" } }))
    await screen.findByRole("button", { name: "Save as new version" })
    await userEvent.clear(screen.getByLabelText("Title"))
    await userEvent.type(screen.getByLabelText("Title"), "Updated returns")
    await userEvent.click(screen.getByRole("button", { name: "Save as new version" }))
    await waitFor(() => expect(requests[1]).toMatchObject({ action: "edit", body: { title: "Updated returns", version: 3 } }))
    app.unmount()
  })

  it("publishes only after confirmation using the article version; archives explicitly", async () => {
    const requests = setup()
    renderApp(`/knowledge/${ARTICLE.id}`)
    await userEvent.click(await screen.findByRole("button", { name: "Publish" }))
    expect(requests).toEqual([])
    const dialog = await screen.findByRole("dialog")
    await userEvent.click(within(dialog).getByRole("button", { name: "Publish article" }))
    await waitFor(() => expect(requests).toEqual([{ action: "publish", body: { version: 3 } }]))
    await userEvent.click(screen.getByRole("button", { name: "Archive" }))
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Archive article" }))
    await waitFor(() => expect(requests[1]).toEqual({ action: "archive", body: { version: 4 } }))
  })

  it("compares immutable versions and opens the cited historical version", async () => {
    setup(false)
    renderApp(`/knowledge/${ARTICLE.id}?version=1`)
    expect(await screen.findByRole("heading", { name: "Version history" })).toBeInTheDocument()
    expect(screen.getByLabelText("Earlier version")).toHaveValue("1")
    expect(screen.getByText("Old policy")).toBeInTheDocument()
    expect(screen.getByLabelText("Title")).toBeDisabled()
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Save as new version" })).not.toBeInTheDocument()
  })

  it("prefills a suggestion draft, converts only on save and dismisses with the expected version", async () => {
    const requests = setup()
    renderApp("/knowledge")
    await userEvent.click(await screen.findByRole("tab", { name: "Suggestions" }))
    await userEvent.click(await screen.findByRole("button", { name: "Dismiss" }))
    await waitFor(() => expect(requests[0]).toEqual({ action: "dismiss", body: { version: 2 } }))
    await userEvent.click(screen.getByRole("button", { name: "Write article" }))
    expect(await screen.findByLabelText("Title")).toHaveValue(suggestion.question_text)
    expect(requests).toHaveLength(1)
    await userEvent.type(screen.getByLabelText("Markdown body"), "We offer a written warranty.")
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }))
    await waitFor(() => expect(requests[1]).toMatchObject({ action: "convert", body: { version: 2, article: { title: suggestion.question_text } } }))
  })

  it("hides authoring and suggestion actions from readers", async () => {
    setup(false)
    renderApp("/knowledge")
    await screen.findByRole("link", { name: "Returns" })
    expect(screen.queryByRole("link", { name: "New article" })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("tab", { name: "Suggestions" }))
    await screen.findByText(suggestion.question_text)
    expect(screen.queryByRole("button", { name: "Write article" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument()
  })

  it("denies a direct route without thread-read permission", async () => {
    mockMe(meWith("org.settings.write"))
    renderApp("/knowledge")
    expect(await screen.findByText("You do not have access to the knowledge base.")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Knowledge" })).not.toBeInTheDocument()
  })
})

it("preserves a draft on conflict, requires reload and never publishes unsaved text", async () => {
  setup()
  let gets = 0
  let patches = 0
  server.use(
    http.get("*/api/v1/knowledge/articles/:id", () => HttpResponse.json({ ...ARTICLE, version: ++gets === 1 ? 3 : 8 })),
    http.patch("*/api/v1/knowledge/articles/:id", () => { patches++; return HttpResponse.json({ code: "knowledge.version_conflict", title: "Conflict", status: 409 }, { status: 409 }) }),
  )
  renderApp(`/knowledge/${ARTICLE.id}`)
  await userEvent.type(await screen.findByLabelText("Title"), " updated")
  expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled()
  await userEvent.click(screen.getByRole("button", { name: "Save as new version" }))
  const dialog = await screen.findByRole("dialog")
  expect(screen.getByLabelText("Title")).toHaveValue("Returns updated")
  expect(patches).toBe(1)
  await userEvent.click(within(dialog).getByRole("button", { name: /Reload/ }))
  await waitFor(() => expect(screen.getByLabelText("Title")).toHaveValue("Returns"))
  await waitFor(() => expect(gets).toBeGreaterThan(1))
})

it("previews markdown as escaped text and rejects executable link protocols", async () => {
  setup()
  renderApp("/knowledge/new")
  const body = await screen.findByLabelText("Markdown body")
  const user = userEvent.setup()
  await user.click(body)
  await user.paste('<img src=x onerror=alert(1)>\n\n[unsafe](javascript:alert)\n\n[safe](https://example.com)')
  const preview = screen.getByRole("region", { name: "Preview" })
  expect(within(preview).queryByRole("img")).not.toBeInTheDocument()
  expect(within(preview).queryByRole("link", { name: "unsafe" })).not.toBeInTheDocument()
  expect(within(preview).getByRole("link", { name: "safe" })).toHaveAttribute("href", "https://example.com")
})
