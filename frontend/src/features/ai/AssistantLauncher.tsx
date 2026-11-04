// Top-bar button and Ctrl/Cmd+J shortcut. The panel itself loads on first use so it stays out of the initial bundle.
import { Sparkles } from "lucide-react"
import { lazy, Suspense, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"

const AssistantPanel = lazy(() => import("./AssistantPanel").then((m) => ({ default: m.AssistantPanel })))

export function AssistantLauncher() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const allowed = permissions.has("ai.assistant.use")
  const [open, setOpen] = useState(false)
  const [everOpened, setEverOpened] = useState(false)

  useEffect(() => {
    if (!allowed) return
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "j") {
        event.preventDefault()
        setEverOpened(true)
        setOpen((o) => !o)
      }
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [allowed])

  if (!allowed) return null
  return (
    <>
      <Button variant="outline" className="min-h-11 sm:min-h-9" title={t("ai.assistant.openTitle")} aria-keyshortcuts="Control+J Meta+J" aria-haspopup="dialog" aria-expanded={open}
        onClick={() => { setEverOpened(true); setOpen(true) }}>
        <Sparkles aria-hidden="true" className="size-4" /> <span className="sr-only sm:not-sr-only">{t("ai.assistant.open")}</span>
      </Button>
      {/* Stays mounted once opened, so an answer that is still arriving is not lost when the panel is closed. */}
      {everOpened ? <Suspense fallback={null}><AssistantPanel open={open} onOpenChange={setOpen} /></Suspense> : null}
    </>
  )
}
