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
