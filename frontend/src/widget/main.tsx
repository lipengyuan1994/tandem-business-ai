// Public visitor widget: /p/:slug/chat and /p/:slug/request. A separate entry so the bundle stays small.
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "@/app/theme.css"
import { SupplierQuote } from "./SupplierQuote"
import { parseLocation, parseQuoteLocation } from "./api"
import { S } from "./strings"
import { Widget } from "./Widget"

const root = document.getElementById("widget")
if (!root) throw new Error("#widget element missing")
const token = parseQuoteLocation(location.pathname)
const where = parseLocation(location.pathname)

createRoot(root).render(
  <StrictMode>{token ? <SupplierQuote key={token} token={token} /> : where ? <Widget slug={where.slug} page={where.page} /> : <p className="p-6 text-sm">{S.notFound}</p>}</StrictMode>,
)
