import { http, HttpResponse } from "msw"
import type { CatalogItem, SalesDocument } from "@/api/types"
import { server } from "@/test/server"

export const ID = "00000000-0000-7000-8000-0000000000d1"
export const CUSTOMER = "00000000-0000-7000-8000-0000000000c1"
export const PERMS = ["sales.document.read", "sales.document.write", "sales.document.issue", "sales.invoice.mark_paid", "sales.invoice.credit", "catalog.item.read", "crm.customer.read"]
export const ITEM: CatalogItem = { id: "00000000-0000-7000-8000-0000000000f1", name: "Brake service", kind: "service", sku: null, description: null, unit: "each", price_minor: 8900, currency: "USD", duration_minutes: 60, buffer_minutes: 0, resource_kind: "staff", is_stock_tracked: false, is_bookable_online: true, status: "active", version: 1 }
export function doc(over: Partial<SalesDocument> = {}): SalesDocument {
  return { id: ID, customer_id: CUSTOMER, bill_to_name: "Ada Example", bill_to_email: "ada@example.com", bill_to_address: "12 Example Lane", currency: "USD", status: "draft", number: null, issue_date: null, due_date: "2026-11-30", notes: null, subtotal_minor: 8900, tax_minor: 734, total_minor: 9634, version: 3, created_at: "2026-10-27T01:00:00Z", kind: "invoice", lines: [{ id: "line1", catalog_item_id: null, name: "Brake service", quantity: "1.000", unit_price_minor: 8900, tax_rate: "0.0825", line_subtotal_minor: 8900, line_tax_minor: 734, line_total_minor: 9634, position: 0 }], ...over }
}
export function mockDocument(d = doc(), family = "invoices") {
  server.use(http.get(`*/api/v1/sales/${family}/${d.id}`, () => HttpResponse.json(d)))
}
export function mockPickers(currency = "USD") {
  server.use(
    http.get("*/api/v1/org/business", () => HttpResponse.json({ id: "business1", name: "Example Cycle Works", legal_name: null, slug: "example-cycle-works", timezone: "America/Denver", locale: "en-US", currency, country: "US", public_profile: {}, status: "active", version: 1 })),
    http.get("*/api/v1/catalog/items", () => HttpResponse.json({ items: [{ ...ITEM, currency }], next_cursor: null })),
    http.get("*/api/v1/crm/customers/match", () => HttpResponse.json([{ customer_id: CUSTOMER, display_name: "Ada Example", reason: "name", score: 0.9 }])),
  )
}
