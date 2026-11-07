# Public bundle

`widget.html` loads public chat and appointment requests at `/p/:slug/{chat,request}`, and supplier quotes at
`/q/:token`. Caddy (production/dev) and Vite (dev/preview) serve this entry for those routes. The supplier
capability selects the business and invitation; no login, staff session API, analytics or tracking is added.

Supplier copy lives in the `supplierQuote` subtree of `src/locales/en.json`, projected at build time by the `virtual:supplier-quote-copy` Vite module without i18next or staff translations.
`QuoteFields.tsx` and `quote-model.ts` are shared with staff manual entry; money uses the existing exact
currency parser. Data access lives in `hooks.ts` and the small typed public client in `api.ts`.

Production JS budget: ≤80 KB gzip including transitive shared chunks, each compressed separately.
Build with `pnpm exec vite build`; start from `dist/widget.html` and include every imported JS chunk when
measuring. Do not count source maps or unrelated staff chunks. The owner runs the browser/a11y e2e tests.
`deploy/scripts/smoke.sh` also checks that `/q/smoke.invalid` serves the widget entry.

PQ-02 measurement: 77,556 bytes gzip (77.56 KB / 75.74 KiB) of transitive JavaScript, using Node
`gzipSync` at default compression per output file. CSS and source maps are excluded from the JS budget.

P10-C1 part 3 displays the backend's `ai_greeting` before/after chat creation, adds Talk to a person through the
visitor-cookie hand-over endpoint, and styles `is_system` messages with a visible label and separate bordered style.
The public API never includes withheld/stopped/cancelled replies. Incremental polling retains its cursor on empty
pages and merges later published replies by the API's publication-time `created_at` projection.
`widget.test.tsx` covers both opening-hours acknowledgements, disclosure and delayed publication after an empty poll.
No staff UI, i18next, Markdown renderer or other new dependency is imported by this entry.

Measured locally for this change with Node gzipSync per transitive JS file: 77,557 → 77,722 bytes (+165 bytes),
within both the 80 KB total JS budget and the requested ~15 KB growth limit. JS plus shared CSS: 88,410 → 88,592 bytes.
The baseline used `pnpm exec vite build` because pre-existing generated-type compatibility errors blocked `tsc -b`;
the final `pnpm build` includes typechecking and passes. This is a build measurement, not a browser/a11y test.

P10-C2b: `/embed/:slug/chat` reuses Widget in compact form; the API client remaps chat to header-token routes
and stores the capability only in iframe sessionStorage with a memory fallback. Parent messaging checks origin,
source and shape; Escape returns focus through the loader. `public/embed.js` is the versioned, cacheable vanilla
loader (≤5 KB gzip), using only its own shadow tree/button/iframe. Caddy fetches tenant frame policy before serving
widget.html; ordinary public/staff pages retain their separate policy. Loader capability headers are log-redacted.
`/p/:slug/bookings` lazily loads Bookings; it removes #t before one POST exchange (StrictMode-safe), then uses the
HttpOnly Strict cookie. Fees bind acknowledgment to the exact text; 409 reloads and requires review. Copy comes
from en.json's bookings subtree through a build-time projection. Real-browser/Caddy/SMTP acceptance is separate.

P10-C2b production measurement (Node gzipSync, each static transitive JS file separately): initial chat/iframe
76,792 bytes; embed.js 1,680 bytes. Bookings and supplier quotes load on their routes;
bookings plus its static dependencies total less than 80 KB (see task card for the exact measurement). Dev Caddy permits Vite's inline React-refresh
preamble only on the embed route; production script-src remains self-only. Live headers remain a reviewer check.

P10-gaps reviewer follow-up (2026-11-07): loader v1.0.1 mounts its closed shadow root only after successful
business config with a nonblank label (at most 80 characters); failures leave no UI and add no console logging.
The panel uses a column flex layout with a nonshrinking header and a shrinking iframe below it. Embedded
heading focus uses preventScroll instead of scrolling to the form. Unit tests cover admission, closed-root
controls/layout rules and focus. The reviewer-run e2e spec checks 1280/375 px geometry, absent UI on the
disallowed host and CSP refusal for a directly injected iframe. Evidence and build sizes: [P10-gaps](../../../docs/roadmap/ai/cards/P10-gaps.md).
