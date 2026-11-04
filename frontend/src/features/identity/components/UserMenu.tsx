import { useNavigate } from "@tanstack/react-router"
import { ChevronDown } from "lucide-react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useLogout } from "../hooks"

export function UserMenu() {
  const { t } = useTranslation()
  const session = useSession()
  const navigate = useNavigate()
  const logout = useLogout()
  const logoutAll = useLogout(true)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm">
          {session.displayName} <span className="sr-only">({t("identity.menu.label")})</span> <ChevronDown aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel className="text-xs text-muted-foreground">{session.businessName}</DropdownMenuLabel>
        <DropdownMenuItem onSelect={() => navigate({ to: "/select-business" })}>{t("identity.menu.switch")}</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => logout.mutate()}>{t("identity.menu.logout")}</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => logoutAll.mutate()}>{t("identity.menu.logoutAll")}</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
