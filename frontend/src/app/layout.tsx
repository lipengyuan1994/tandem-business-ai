import { lazy, Suspense, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Link, Outlet } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"
import { useCounters } from "@/features/dashboard/hooks"
import { AssistantLauncher } from "@/features/ai/AssistantLauncher"
import { UserMenu } from "@/features/identity/components/UserMenu"
import { NotificationBell } from "@/features/notifications/NotificationBell"
import { NAV_ITEMS, hasPermission } from "@/lib/permissions"
import { useSession } from "./session-context"

const MobileNavigation = lazy(() => import("./MobileNavigation").then((m) => ({ default: m.MobileNavigation })))

export function AppLayout() {
  const { t } = useTranslation()
  const session = useSession()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuButton = useRef<HTMLButtonElement>(null)
  const items = NAV_ITEMS.filter((item) => hasPermission(session.permissions, item.permission))
  const counters = useCounters(session.businessId !== null)

  const navigation = (onNavigate?: () => void) => (
    <nav aria-label={t("app.mainNav")}>
          <ul className="space-y-0.5 px-2">
            {items.map((item) => (
              <li key={item.to}>
                <Link
                  to={item.to}
                  onClick={onNavigate}
                  className="block min-h-11 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  activeProps={{ className: "bg-accent text-accent-foreground font-medium", "aria-current": "page" }}
                >
                  <span className="flex items-center justify-between">
                    {t(item.labelKey)}
                    {item.counter && counters.data?.[item.counter] ? (
                      <span className="rounded-full bg-primary px-1.5 text-xs text-primary-foreground" aria-label={t("app.countLabel", { n: counters.data[item.counter] })}>
                        {counters.data[item.counter]}
                      </span>
                    ) : null}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>
  )

  return (
    <div className="flex min-h-dvh">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-background focus:px-3 focus:py-2">
        {t("app.skipToContent")}
      </a>
      <aside className="hidden w-56 shrink-0 border-r bg-card md:block">
        <div className="px-4 py-4">
          <p className="text-sm font-semibold">{t("app.name")}</p>
          {session.businessName ? <p className="text-xs text-muted-foreground">{session.businessName}</p> : null}
        </div>
        {navigation()}
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-end gap-2 border-b px-4 py-2">
          <Button ref={menuButton} variant="outline" className="mr-auto md:hidden" aria-haspopup="dialog" aria-expanded={menuOpen} onClick={() => setMenuOpen(true)}>{t("app.openMenu")}</Button>
          {menuOpen ? <Suspense fallback={null}><MobileNavigation title={t("app.mainNav")} description={session.businessName ?? t("app.name")} onClose={() => setMenuOpen(false)} returnFocus={() => menuButton.current?.focus()}>{navigation(() => setMenuOpen(false))}</MobileNavigation></Suspense> : null}
          <AssistantLauncher />
          <NotificationBell />
          <UserMenu />
        </header>
        <main id="main" className="flex-1 p-4 md:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
