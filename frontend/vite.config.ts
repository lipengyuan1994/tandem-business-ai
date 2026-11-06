/// <reference types="vitest/config" />
import { readFileSync } from 'node:fs'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { type Connect, type Plugin, defineConfig } from 'vite'

// The visitor widget is its own entry (widget.html) so its bundle stays small; /p/:slug/* serves it.
const widgetRewrite: Connect.NextHandleFunction = (req, _res, next) => {
  if (req.url && (/^\/p\/[a-z0-9-]+\/(chat|request|bookings)\/?(\?.*)?$/.test(req.url) || /^\/embed\/[a-z0-9-]+\/chat\/?(\?.*)?$/.test(req.url) || /^\/q\/[A-Za-z0-9_.-]{1,600}\/?(\?.*)?$/.test(req.url))) req.url = '/widget.html'
  next()
}
const publicWidget: Plugin = {
  name: 'public-widget-route',
  configureServer: (server) => void server.middlewares.use(widgetRewrite),
  configurePreviewServer: (server) => void server.middlewares.use(widgetRewrite),
}

// Keep en.json as the copy source without shipping all staff translations in the public bundle.
const supplierCopy: Plugin = {
  name: 'supplier-quote-copy',
  resolveId: (id) => id === 'virtual:supplier-quote-copy' ? '\0supplier-quote-copy' : undefined,
  load(id) {
    if (id !== '\0supplier-quote-copy') return
    const path = `${import.meta.dirname}/src/locales/en.json`
    this.addWatchFile(path)
    const copy = JSON.parse(readFileSync(path, 'utf8')) as { supplierQuote: Record<string, string> }
    return `export default ${JSON.stringify(copy.supplierQuote)}`
  },
}

const bookingCopy: Plugin = {
  name: 'booking-copy',
  resolveId: (id) => id === 'virtual:booking-copy' ? '\0booking-copy' : undefined,
  load(id) {
    if (id !== '\0booking-copy') return
    const path = `${import.meta.dirname}/src/locales/en.json`
    this.addWatchFile(path)
    const copy = JSON.parse(readFileSync(path, 'utf8')) as { bookings: Record<string, unknown> }
    return `export default ${JSON.stringify(copy.bookings)}`
  },
}

// In the dev Compose stack the browser reaches Vite through Caddy on https://localhost:8443.
const hmrClientPort = process.env.VITE_HMR_CLIENT_PORT ? Number(process.env.VITE_HMR_CLIENT_PORT) : undefined

export default defineConfig({
  plugins: [react(), tailwindcss(), publicWidget, supplierCopy, bookingCopy],
  resolve: { alias: { '@': `${import.meta.dirname}/src` } },
  server: {
    allowedHosts: ['localhost', 'web'],
    hmr: hmrClientPort ? { clientPort: hmrClientPort, protocol: 'wss' } : undefined,
  },
  build: {
    sourcemap: true,
    rolldownOptions: {
      input: { main: `${import.meta.dirname}/index.html`, widget: `${import.meta.dirname}/widget.html` },
      output: {
        // Both entries need React. Keep its runtime together to avoid extra preload/compression overhead.
        codeSplitting: { groups: [{ name: 'react-runtime', test: /\/node_modules\/(?:react|react-dom|scheduler)\// }] },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Run as if the browser were in UTC, so pages must use the business time zone explicitly (09 Times).
    env: { TZ: 'UTC' },
    css: false,
    testTimeout: 15_000,
  },
})
