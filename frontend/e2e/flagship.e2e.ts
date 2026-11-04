// The flagship workflow without AI (01-product-and-scope.md): a visitor writes in, staff propose a booking with a part
// and a reply, a second person approves in the UI, the worker executes, and every screen shows the result.
// Until the assistant exists (P5) the proposal is created through the API, exactly as the assistant will create it.
import { expect, test } from "@playwright/test"
import { storageState } from "./support/accounts.ts"
import { api } from "./support/api.ts"

interface Slot { starts_at: string; resource_id: string; local_label: string }
interface Level { item_name: string; available: string }

test("visitor request → proposal → approval in the UI → booked, reserved, replied", async ({ browser, baseURL }) => {
  const visitor = await browser.newContext({ baseURL, ignoreHTTPSErrors: true })
  const caseyCtx = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, storageState: storageState("casey") })
  const morganCtx = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, storageState: storageState("morgan") })
  const casey = caseyCtx.request

  // 1. A visitor writes in through the public widget.
  const chat = await visitor.newPage()
  await chat.goto("/p/example-cycle-works/chat")
  await expect(chat.getByText(/Our staff read your messages/)).toBeVisible() // privacy notice
  await chat.getByLabel("Your name").fill("Ada Example")
  await chat.getByLabel("Email").fill("ada@example.com")
  await chat.getByLabel("Message").fill("My rear brake squeaks. Can you look at it next week?")
  await chat.getByRole("button", { name: "Send message" }).click()
  await expect(chat.getByRole("listitem").filter({ hasText: "My rear brake squeaks." })).toBeVisible() // in the thread, not an error
  await expect(chat.getByRole("alert")).toHaveCount(0)

  // 2. Casey (operator) finds the conversation, the customer, the service and a free slot; holds it and proposes.
  const inbox = await api<{ items: { id: string; visitor_email: string | null }[] }>(casey, "GET", "/api/v1/conversations?status=waiting_on_staff")
  const conversation = inbox.items.find((c) => c.visitor_email === "ada@example.com")!
  const [ada] = await api<{ customer_id: string }[]>(casey, "GET", "/api/v1/crm/customers/match?email=ada@example.com")
  const services = await api<{ items: { id: string; name: string }[] }>(casey, "GET", "/api/v1/catalog/items?kind=service&q=Brake")
  const brake = services.items.find((s) => s.name === "Brake service")!
  const earliest = new Date(Date.now() + 2 * 86_400_000).toISOString()
  const latest = new Date(Date.now() + 14 * 86_400_000).toISOString()
  const [slot] = await api<Slot[]>(casey, "GET", `/api/v1/scheduling/slots?service_id=${brake.id}&earliest=${earliest}&latest=${latest}&limit=1`)
  expect(slot, "a free slot in the next two weeks").toBeTruthy()
  const hold = await api<{ id: string }>(casey, "POST", "/api/v1/scheduling/holds", { service_id: brake.id, resource_id: slot!.resource_id, customer_id: ada!.customer_id, starts_at: slot!.starts_at })
  const reply = `You're booked for ${slot!.local_label}. See you then!`
  const proposal = await api<{ id: string }>(casey, "POST", "/api/v1/approvals/proposals", {
    action_type: "scheduling.book_appointment", title: "Book Ada's brake service",
    payload: { customer: { id: ada!.customer_id }, service_id: brake.id, resource_id: slot!.resource_id, starts_at: slot!.starts_at, hold_id: hold.id,
      reply: { conversation_id: conversation.id, body: reply } },
    evidence: [{ kind: "message", excerpt: "My rear brake squeaks. Can you look at it next week?" }],
  })

  // 3. Morgan (owner) reviews the preview and approves the exact revision in the UI.
  const morgan = await morganCtx.newPage()
  const padsBefore = Number((await api<Level[]>(morgan.request, "GET", "/api/v1/inventory/levels")).find((l) => l.item_name === "Brake pads (pair)")!.available)
  await morgan.goto(`/approvals/${proposal.id}`)
  await expect(morgan.getByRole("heading", { name: "Book Ada's brake service" })).toBeVisible()
  await expect(morgan.getByText("My rear brake squeaks. Can you look at it next week?")).toBeVisible() // evidence
  await morgan.getByRole("button", { name: /^Approve:/ }).click()
  await expect(morgan.getByText("Succeeded").first()).toBeVisible({ timeout: 30_000 }) // the worker executes in the background

  // 4. Results everywhere: schedule, stock, the visitor's chat, the customer's timeline.
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Denver" }).format(new Date(slot!.starts_at))
  await morgan.goto(`/schedule?date=${day}`)
  await expect(morgan.getByRole("button", { name: /Brake service, Ada Example, Confirmed/ })).toBeVisible()
  const padsAfter = Number((await api<Level[]>(morgan.request, "GET", "/api/v1/inventory/levels")).find((l) => l.item_name === "Brake pads (pair)")!.available)
  expect(padsAfter).toBe(padsBefore - 1)
  await expect(chat.getByText(reply)).toBeVisible({ timeout: 15_000 }) // the widget polls every 5 s
  await morgan.goto(`/customers/${ada!.customer_id}`)
  await expect(morgan.getByText(/Brake service/).first()).toBeVisible()

  await Promise.all([visitor.close(), caseyCtx.close(), morganCtx.close()])
})
