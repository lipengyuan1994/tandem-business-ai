# 09 · Frontend

## Stack (fixed by [ADR-005](03-decisions.md#adr-005-frontend-react--typescript--vite-single-page-app))

React 19 · TypeScript strict · Vite · TanStack Router (file-free, code-defined typed routes) · TanStack Query · TanStack Table · shadcn/ui (Radix) + Tailwind CSS v4 · react-hook-form + zod · i18next · `openapi-typescript` + `openapi-fetch` · `date-fns` + `date-fns-tz` · `lucide-react` icons · Vitest + Testing Library + MSW · Playwright · pnpm.

## Application shell

```
┌──────────────────────────────────────────────────────────────────────────┐
│ [Business ▾]  Search (Ctrl K)                    🔔 3   ✨ Assistant   👤 │  top bar
├────────────┬─────────────────────────────────────────────────────────────┤
│ Today      │                                                             │
│ Inbox  (4) │                page content                                 │
│ Approvals(2)│                                                            │
│ Customers  │                                                             │
│ Schedule   │                                       ┌───────────────────┐ │
│ Inventory  │                                       │ Assistant panel   │ │
│ Catalog    │                                       │ (slide-over, SSE) │ │
│ Marketing  │                                       └───────────────────┘ │
│ Audit      │                                                             │
│ Settings   │                                                             │
└────────────┴─────────────────────────────────────────────────────────────┘
```

- Navigation items are hidden when the user lacks the module's read permission (from `/auth/me`).
- Counts (Inbox, Approvals) come from `/api/v1/dashboard/counters` and are polled every 30 s. Polling is enough for SMB scale. Server-sent events for live counters are a v1 enhancement.
- Responsive: full layout ≥ 1024 px; collapsible sidebar from 768–1023 px (tablet at the front desk); below 768 px a bottom navigation with Today, Inbox, Approvals and Schedule (approve on the go).

## Page map

| Route | Page | Module | Key components | Phase |
|---|---|---|---|---|
| `/login` | Redirect to Keycloak | identity | — | P2 |
| `/select-business` | Business picker | identity | Card list | P2 |
| `/today` | Today | dashboard | Counters, agenda, low stock, pending approvals, AI briefing (P5) | P3/P5 |
| `/inbox`, `/inbox/:id` | Conversations | conversations | Thread list with filters, message thread, composer, customer side card, AI suggestions (P5) | P3 |
| `/approvals`, `/approvals/:id` | Approval queue and detail | approvals | Preview diff, evidence list, revision history, approve/reject with comment, edit payload form per action type | P2/P3 |
| `/customers`, `/customers/:id` | Customers | crm | DataTable with search, profile, timeline, consent panel, notes, appointments | P3 |
| `/schedule` | Calendar | scheduling | `ResourceDayGrid` (columns = resources, rows = 15-min slots, local time), week view, holds shown hatched, drag-to-reschedule (v1) | P3 |
| `/schedule/new`, `/appointments/:id` | Appointment form and detail | scheduling | Service, customer, resource, slot finder, parts check | P3 |
| `/catalog` | Services and products | catalog | DataTable, item form, service parts editor | P3 |
| `/inventory`, `/inventory/:itemId` | Stock levels and item ledger | inventory | On hand / reserved / available columns, movements ledger, reservations, adjust dialog, reorder rule | P3 |
| `/marketing`, `/marketing/:id` | Campaigns | marketing | Segment builder (whitelisted filters), audience preview with excluded counts by reason, content revisions | P6 |
| `/audit` | Audit log | audit | Filterable table, event detail drawer, CSV export | P2 |
| `/settings/*` | Business, locations and hours, staff and roles, AI policy and budget, notifications, data | org, identity, ai | Forms | P2/P5 |
| `/system` | Health, jobs, backups, version | admin_ops | Status cards | P8 |
| `/p/:slug/chat` | Public chat | conversations (public) | Minimal chat UI, consent notice, privacy link | P3 (UI) / P5 (AI) |
| `/p/:slug/request` | Public appointment request | conversations (public) | Service picker, preferred times, contact form (creates a conversation, not a booking) | P3 |

## Data access pattern

```ts
// src/api/client.ts — the only place fetch happens
export const api = createClient<paths>({ baseUrl: "/api/v1", credentials: "same-origin" });
api.use(csrfMiddleware);     // adds X-CSRF-Token for unsafe methods
api.use(problemMiddleware);  // converts problem+json into ApiProblem errors

// src/features/crm/hooks.ts
export function useCustomer(id: string) {
  return useQuery({ queryKey: ["crm", "customer", id], queryFn: () => unwrap(api.GET("/crm/customers/{customer_id}", { params: { path: { customer_id: id } } })) });
}
export function useUpdateCustomer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: components["schemas"]["CustomerUpdate"] & { id: string }) =>
      unwrap(api.PATCH("/crm/customers/{customer_id}", { params: { path: { customer_id: body.id } }, body })),
    onSuccess: (c) => qc.setQueryData(["crm", "customer", c.id], c),
  });
}
```

- **Query keys:** `[module, resource, id | params]`. After a mutation, invalidate the module's keys.
- **Conflicts (409):** show a standard dialog ("This record changed since you opened it") with *Reload* and *Copy my changes*. Never auto-overwrite.
- **Validation errors (422):** map `errors[].field` onto form fields.
- **Times:** the API returns UTC. Display with `formatInZone(ts, business.timezone)`. Inputs are in business local time and converted to UTC before sending, with the zone always shown ("10:00 MT").
- **Money:** `MoneyText` formats `{amount_minor, currency}` with `Intl.NumberFormat`.

## Design system

- Tailwind tokens in `src/app/theme.css`: neutral base, one brand accent, semantic colours (success, warning, danger, info) and status colours shared by every status badge (`pending_review` amber, `approved` blue, `succeeded` green, `failed` red, `expired` grey). Light and dark themes.
- **Approvals UI rules:** the preview diff is the hero. Every AI-originated item shows an "AI proposed" badge with the evidence list. The approve button text names the effect ("Approve: book Tue 10:00 + reserve 1 × Brake pads + send reply"). Destructive or external effects are listed separately and say *simulated* when the adapter is simulated.
- Shared components in `components/common/`: `PageHeader`, `DataTable` (server pagination), `EmptyState`, `StatusBadge`, `MoneyText`, `LocalTime`, `ConfirmDialog`, `ConflictDialog`, `PermissionGate`, `EvidenceList`, `PreviewDiff`.

## Accessibility and quality bar ("production level")

- WCAG 2.2 AA: keyboard reachable everything, visible focus, labels on every input, colour contrast ≥ 4.5:1, `aria-live` for toasts and assistant streaming, and reduced-motion support.
- `@axe-core/playwright` runs in end-to-end tests on every main page. Zero serious violations is a gate.
- Performance budget: initial JS ≤ 250 KB gzip for the staff app shell, with route-level code splitting. The public widget bundle is separate at ≤ 80 KB gzip.
- Every list has loading, empty and error states. Every mutation has pending, success and error feedback.
- No `any` in TypeScript (`eslint` rule). No inline API calls outside `hooks.ts`.

## Frontend testing

| Level | Tool | What |
|---|---|---|
| Unit | Vitest | `lib/` helpers (time zones, money, problem mapping) |
| Component | Vitest + Testing Library + MSW | Each page renders loading, empty, data and error states; forms validate; permission gating |
| End to end | Playwright | Flagship workflow (approve booking), conflict dialog, login with TOTP (seeded test secret), cross-business switch, accessibility scan |

## As built (P3, 2026-11-03)

- **Pages:** `/today`, `/inbox(/:id)`, `/customers(/:id)`, `/schedule?date&appointment`, `/inventory(/:itemId)` and `/catalog` are in `features/<module>/`. Each feature has its own `hooks.ts`; features may import another feature's hooks or components (for example the slot finder uses catalog and crm hooks).
- **Code splitting:** every page except Today loads through `lazyRouteComponent`. Measured initial JavaScript for the staff app is about 194 KiB gzip (budget 250).
- **Public widget:** a second Vite entry, `widget.html` → `src/widget/`. It has no router, React Query or i18next, and its copy is in `src/widget/strings.ts`. It measures about 74 KB gzip of JavaScript (budget 80). Vite dev and preview rewrite `/p/:slug/(chat|request)` to `widget.html`. **The production web server must do the same rewrite (task P4-02).**
- **Time zones:** Vitest runs with `TZ=UTC` (`vite.config.ts`), so any page that formats with the browser zone fails its tests. `features/scheduling/grid.ts` holds the pure day-bounds and placement helpers (DST days are 23 or 25 hours).
- **Approval edit forms:** `features/approvals/components/forms/` maps action types to custom forms (booking slot picker, reply text, stock adjustment). Other types fall back to the generic `RevisionEditor`.
- **Conflicts:** edit dialogs keep what the user typed when "Reload" is pressed; the next save uses the new version.
- **End to end:** `make e2e` runs Playwright (`frontend/e2e/`) against the running dev stack. Global setup recreates the demo data (`seed --reset`) and gives the seeded operator Casey a fresh dev login (`app.cli dev-account`). The `login` project signs Morgan and Casey in through Keycloak, including password change and TOTP enrolment, and saves their sessions to the gitignored `e2e/.auth/`. The tests cover the flagship flow (approval in the UI), the conflict dialog, business-switch isolation and axe scans of the six main pages plus the widget. Until the assistant exists, the flagship proposal is created through the API.

- **End to end against another stack (P4-07):** `E2E_COMPOSE_FILE`, `E2E_ENV_FILE` and `E2E_FRESH_ACCOUNTS=1` point global setup at the production rehearsal (`deploy/scripts/rehearse.sh`), where Morgan also gets a fresh login (`e2e/.auth/morgan.json`).

## Procurement screens (PQ-02, PRE)

Purchasing includes `/purchasing?tab=quotes`, `/purchasing/quote-requests/:id`, manual quote entry and
backend-ranked comparison with explained components. New writes link to their approval proposals; approving
a quote conversion creates a draft PO. `/purchasing?late=true` and a purchasing-read-gated Today card use
the dedicated late-order API. Public supplier responses at `/q/:token` use the separate widget bundle,
with copy from the `supplierQuote` subtree in `en.json`, no i18next runtime, login or tracking.
Both Caddyfiles and Vite dev/preview route `/q/*` to `widget.html`. Component tests cover the forms and
endpoint/version bindings; owner-run e2e includes the supplier response, comparison and draft PO flow,
plus accessibility checks for the quote tab and public pages. See `frontend/src/features/purchasing/README.md`.

## AI screens (AI-13, EVENT)

The assistant panel (top-bar button, Ctrl/Cmd+J), "Summarise" in the Inbox, AI badges and an evidence note on proposals,
the Today briefing, "Paste supplier reply" on quote requests, "Ask AI to request quotes" on low-stock items and
`/settings/ai` live in `src/features/ai/` (see its `README.md`). The panel is a lazy chunk (about 2.3 KB gzip); the
launcher and `ai.*` copy add about 5 KiB gzip to the initial bundle (205.2 → 209.9 KiB). Copilot answers stream over
`fetch` (SSE) so the CSRF header can be sent. AI endpoints not yet in the generated schema use hand-written types
until `make gen-api` includes them.
