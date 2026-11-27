// Public visitor widget: /p/:slug/chat and /p/:slug/request, supplier quotes (/q/) and invoice pay links (/pay/). A separate entry so the bundle stays small.
import { StrictMode, Suspense } from "react"
import { createRoot } from "react-dom/client"
import "@/app/theme.css"
import { parseLocation, parsePayLocation, parseQuoteLocation } from "./api"
import { S } from "./strings"
import { LazyBookings, LazyPayPage, LazySupplierQuote } from "./LazyPages"
import { EmbedChat } from "./EmbedChat"
import { Widget } from "./Widget"

const root = document.getElementById("widget")
if (!root) throw new Error("#widget element missing")
const token = parseQuoteLocation(location.pathname)
const payToken = parsePayLocation(location.pathname)
const where = parseLocation(location.pathname)
const bookings = /^\/p\/([a-z0-9-]+)\/bookings\/?$/.exec(location.pathname)
const embed = /^\/embed\/([a-z0-9-]+)\/chat\/?$/.exec(location.pathname)

createRoot(root).render(
  <StrictMode><Suspense fallback={<p className="p-6 text-sm">{S.loading}</p>}>{bookings ? <LazyBookings key={bookings[1]} slug={bookings[1]!} /> : embed ? <EmbedChat slug={embed[1]!} /> : token ? <LazySupplierQuote key={token} token={token} /> : payToken ? <LazyPayPage key={payToken} token={payToken} /> : where ? <Widget slug={where.slug} page={where.page} /> : <p className="p-6 text-sm">{S.notFound}</p>}</Suspense></StrictMode>,
)
