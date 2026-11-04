import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./app/theme.css"
import "./app/i18n"
import { App } from "./app/providers"

const root = document.getElementById("root")
if (!root) throw new Error("#root element missing")

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
