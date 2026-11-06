import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "@/api/client"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const draft = {
  id: "00000000-0000-7000-8000-0000000000d1", title: "Pets policy", slug: "pets", status: "draft", visibility: "public",
  current_version: 1, published_version: null, version: 1, created_by: null, source: "pdf_import", source_filename: "policy.pdf",
  source_page_start: 1, source_page_end: 2, search_index_state: "not_applicable",
  created_at: "2026-11-06T17:00:00Z", updated_at: "2026-11-06T17:00:00Z", versions: [],
}

beforeEach(async () => {
  mockMe(meWith("conversations.thread.read", "org.settings.write"))
  const { File: StreamingFile } = await vi.importActual<{ File: typeof File }>("node:buffer")
  const form = await new Response("", { headers: { "Content-Type": "application/x-www-form-urlencoded" } }).formData()
  vi.stubGlobal("File", StreamingFile)
  vi.stubGlobal("FormData", form.constructor)
  setCsrfToken("synthetic-csrf")
  server.use(http.get("*/api/v1/knowledge/articles", () => HttpResponse.json([])))
})
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })

it("imports multipart PDF with CSRF, shows progress and draft review links without publishing", async () => {
  let release: () => void = () => {}
  const ready = new Promise<void>((resolve) => { release = resolve })
  let requestFile: FormDataEntryValue | null = null
  let csrf: string | null = null
  server.use(http.post("*/api/v1/knowledge/imports", async ({ request }) => {
    csrf = request.headers.get("X-CSRF-Token")
    requestFile = (await request.formData()).get("file")
    await ready
    return HttpResponse.json({ drafts: [draft] }, { status: 201 })
  }))
  renderApp("/knowledge")
  const input = await screen.findByLabelText("PDF file")
  await userEvent.upload(input, new File(["%PDF-synthetic"], "policy.pdf", { type: "application/pdf" }))
  await userEvent.click(screen.getByRole("button", { name: "Import drafts" }))
  expect(await screen.findByText("Uploading and extracting PDF…", { selector: "p" })).toHaveAttribute("role", "status")
  expect(input).toBeDisabled()
  release()
  expect(await screen.findByRole("link", { name: "Pets policy" })).toHaveAttribute("href", `/knowledge/${draft.id}`)
  expect(screen.getByText(/Nothing has been published/)).toBeInTheDocument()
  expect(screen.getByText("Pages 1–2")).toBeInTheDocument()
  expect(csrf).toBe("csrf-test-token")
  expect((requestFile as unknown as File).name).toBe("policy.pdf")
})

it("shows parser errors and rejects oversize files before upload", async () => {
  let calls = 0
  server.use(http.post("*/api/v1/knowledge/imports", () => {
    calls++
    return HttpResponse.json({ code: "knowledge.pdf_encrypted", title: "Encrypted PDF", status: 422 }, { status: 422 })
  }))
  renderApp("/knowledge")
  const input = await screen.findByLabelText("PDF file")
  await userEvent.upload(input, new File(["%PDF-"], "encrypted.pdf", { type: "application/pdf" }))
  await userEvent.click(screen.getByRole("button", { name: "Import drafts" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("This PDF is encrypted")
  await userEvent.upload(input, new File([new Uint8Array(10 * 1024 * 1024 + 1)], "big.pdf", { type: "application/pdf" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("Choose a PDF no larger than 10 MB")
  expect(screen.getByRole("button", { name: "Import drafts" })).toBeDisabled()
  expect(calls).toBe(1)
})

it("hides PDF import from readers and displays indexed, pending and unavailable states", async () => {
  mockMe(meWith("conversations.thread.read"))
  server.use(http.get("*/api/v1/knowledge/articles", () => HttpResponse.json(
    ["indexed", "pending", "unavailable"].map((state) => ({ ...draft, id: state, title: state, status: "published", search_index_state: state })),
  )))
  renderApp("/knowledge")
  expect(await screen.findByText("Indexed for search")).toBeInTheDocument()
  expect(screen.getByText("Search index pending (full-text only)")).toBeInTheDocument()
  expect(screen.getByText("Search index unavailable (full-text only)")).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByLabelText("PDF file")).not.toBeInTheDocument())
})
