# Purchasing

Lazy routes `/purchasing` and `/purchasing/purchase-orders/$id`.
Purchase orders, suppliers and reorder suggestions use the generated API client via `hooks.ts`.
All reads require `purchasing.po.read`; mutations and override controls are permission-gated.
Costs are edited in decimal money and sent as integer minor units. Stored totals come from the API.
PO transitions require confirmation. Edit conflicts preserve input while reloading versions.
Each receipt attempt freezes a UUID idempotency key and its request for lost-response retries.
Unknown outcomes and conflicts block new receipts until reload shows the recorded deliveries.
Starting another receipt after that review clears quantities and uses a new key.
Failed supplier reloads keep conflict recovery open and report the error, even with cached data.
Receipt history displays delivery quantities and override reasons in business local time.
Tests cover page states, permissions, suggestion creation, approval confirmation and receipt entry.
Playwright covers suggestion → approval → sent → partial/full receipt → stock increase;
`/purchasing` is also included in the shared accessibility suite.

## Quote procurement (PQ-02, PRE)

`/purchasing?tab=quotes` lists quote requests, answered supplier counts and reply dates.
Request quotes accepts distinct stock items and positive quantities, needed/reply dates, an optional message,
and 1–5 suppliers with email addresses that sell every selected item. Dates use the business-local day.
`/purchasing/quote-requests/$id` shows invitations, queued follow-up times, received offers and comparison.
Manual entry uses the same decimal-price fields as the public supplier form, tagged `source=staff`.

All four new staff writes create **approval proposals**. The success message links to Review approval;
the operation takes effect only after an authorized colleague approves and the worker executes it.
Identical retries reuse the proposal idempotency key while the screen is mounted. A PO proposal binds the
exact quote ID/version; after approval it produces a draft PO which still needs normal PO approval and send.

Comparison displays the backend's order, totals, delivery dates, lateness, price/delivery components,
reliability history/default, score and all ineligibility reasons. It does not recalculate or reorder scores.
The one-line formula explains the weights; supporting text explains normalization and the reliability fallback.
All eleven columns stay visible. Headings wrap (`DataTable wrapHeaders`), dates do not break, and the create-PO button has
a two-line label with a 10 rem minimum, so the table and its button fit a 1280 px window beside the sidebar without scrolling
(the demo may be recorded at that size). Narrower windows scroll inside the table's own labelled region, never the page;
`e2e/quote-comparison.e2e.ts` checks 1280, 1440 and 375 px and runs axe on each.
The late-order filter and permission-gated Today card use `/purchasing/late-purchase-orders`, including
orders beyond the first purchase-order page. Unknown delivery dates are not inferred to be late.
Last follow-up means queued, not confirmed delivered. Both invitation and late-PO follow-ups require approval.

Each supplier row that has not answered (request still open, write permission) has *Copy link*
(`SupplierLink.tsx`): it asks the API for that supplier's signed page, writes it to the clipboard and toasts. The link
works like a password, so it is never rendered, cached (`gcTime: 0`) or put in a URL; only if the browser refuses to copy
does a dialog show it for copying by hand. `e2e/supplier-link.e2e.ts` copies it and opens the form on a phone.

The public `/q/:token` page belongs to `src/widget/`, without staff login or tracking. It accepts prices in
normal currency units, availability, lead time, validity, minimum quantity and notes, or a decline.
The existing exact money parser converts to minor units without floating-point multiplication.
Invalid/expired links and closed requests have clear messages; failed submissions retain input.
Caddy production/dev and Vite dev/preview all route `/q/*` to `widget.html`.

MSW tests: `quotes.test.tsx`, `src/widget/SupplierQuote.test.tsx`, and the purchasing card in Today tests.
Owner-run e2e: `procurement.e2e.ts` requests quotes in the UI, has Casey approve Morgan's proposals,
submits a real public supplier response, compares and creates a draft PO. The fixture temporarily grants
Casey admin rights and restores the original roles in `finally`. The local-stack helper reads the actual
synthetic invitation from the tenant-scoped outbox in the API container because HTTP APIs hide tokens.
It follows `E2E_COMPOSE_FILE`/`E2E_ENV_FILE` from global setup and never prints the capability.
Capability-bearing e2e traces/screenshots are disabled. A11y covers the quote tab, invalid-token page and
live supplier form (phone viewport). Run `make e2e` on the owner's stack; no hosted services are needed.
