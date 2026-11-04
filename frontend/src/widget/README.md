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
