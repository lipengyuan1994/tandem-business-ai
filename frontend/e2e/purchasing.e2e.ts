import { expect, test } from "@playwright/test"
import { storageState } from "./support/accounts.ts"
import { api } from "./support/api.ts"

test.use({ storageState: storageState("morgan") })
interface Level { item_id: string; item_name: string; on_hand: string }

test("suggestion → approved PO → sent → partial and full receipts increase stock", async ({ page }) => {
  // Inner tubes avoid sharing the brake-pad ledger with the flagship and sales tests.
  const levels = await api<Level[]>(page.request, "GET", "/api/v1/inventory/levels")
  const tube = levels.find((l) => l.item_name === "Inner tube 700x28")
  if (!tube) throw new Error("Seeded inner tube stock is missing")
  const before = Number(tube.on_hand)
  await api(page.request, "PUT", `/api/v1/inventory/reorder-rules/${tube.item_id}`, { reorder_point: String(before + 5), target_level: String(before + 15) })
  await page.goto("/purchasing")
  await expect(page.getByRole("heading", { name: "Purchasing", level: 1 })).toBeVisible()
  await page.getByRole("tab", { name: "Reorder suggestions" }).click()
  await page.getByRole("button", { name: "Create PO · Example Tyre Co.", exact: true }).click()
  const create = page.getByRole("dialog", { name: "New purchase order", exact: true })
  await create.getByLabel("Quantity", { exact: true }).fill("10")
  await expect(create.getByLabel("Unit cost (USD)")).toHaveValue("5.00")
  await create.getByRole("button", { name: "Create draft", exact: true }).click()
  await expect(create).toBeHidden()
  await expect(page).toHaveURL(/\/purchasing\/purchase-orders\/[^/]+$/)
  for (const [button, title] of [["Approve PO", "Approve this purchase order?"], ["Mark as sent", "Mark this purchase order as sent?"]]) {
    await page.getByRole("button", { name: button, exact: true }).click()
    const confirm = page.getByRole("dialog", { name: title, exact: true })
    await confirm.getByRole("button", { name: button, exact: true }).click()
    await expect(confirm).toBeHidden()
  }
  await expect(page.getByRole("heading", { name: /^PO-2026-\d{4}$/, level: 1 })).toBeVisible()
  for (const [quantity, status] of [["4", "Partially received"], ["6", "Received"]]) {
    await page.getByRole("button", { name: "Receive goods", exact: true }).click()
    const receive = page.getByRole("dialog", { name: "Receive goods", exact: true })
    await receive.getByLabel("Quantity received · Inner tube 700x28", { exact: true }).fill(quantity)
    await receive.getByRole("button", { name: "Record receipt", exact: true }).click()
    await expect(receive).toBeHidden()
    await expect(page.locator("main header").getByText(status, { exact: true })).toBeVisible()
  }
  await expect(page.getByRole("region", { name: "Receipt history" }).getByText(/^Inner tube 700x28:/)).toHaveCount(2)
  const after = await api<Level[]>(page.request, "GET", "/api/v1/inventory/levels")
  expect(Number(after.find((l) => l.item_id === tube.item_id)?.on_hand)).toBe(before + 10)
})
