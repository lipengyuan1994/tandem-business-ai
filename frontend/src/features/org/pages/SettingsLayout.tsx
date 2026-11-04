import { Link, Outlet } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { PageHeader } from "@/components/common/PageHeader"

const TABS = [
  { to: "/settings/business", key: "settings.tabs.business", permission: "org.business.read" },
  { to: "/settings/locations", key: "settings.tabs.locations", permission: "org.location.read" },
  { to: "/settings/policies", key: "settings.tabs.policies", permission: "org.settings.read" },
  { to: "/settings/staff", key: "settings.tabs.staff", permission: "identity.membership.read" },
  { to: "/settings/ai", key: "ai.settings.tab", permission: "ai.settings.read" },
] as const

export function SettingsLayout() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  return (
    <>
      <PageHeader title={t("nav.settings")} />
      <nav aria-label={t("nav.settings")} className="mb-6 grid grid-cols-2 gap-1 border-b md:flex md:flex-wrap">
        {TABS.filter((tab) => permissions.has(tab.permission)).map((tab) => (
          <Link key={tab.to} to={tab.to} className="-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground hover:text-foreground"
            activeProps={{ className: "border-primary bg-accent text-accent-foreground font-semibold", "aria-current": "page" }}>
            {t(tab.key)}
          </Link>
        ))}
      </nav>
      <Outlet />
    </>
  )
}
