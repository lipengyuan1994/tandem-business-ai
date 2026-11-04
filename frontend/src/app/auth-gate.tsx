// Loads /auth/me and provides the session. Not signed in → Keycloak login; no active business → picker.
import { Navigate, useRouterState } from "@tanstack/react-router"
import { useEffect, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { Skeleton } from "@/components/ui/skeleton"
import { QueryError } from "@/components/common/QueryError"
import { useMe } from "@/features/identity/hooks"
import { TerminologyProvider } from "./TerminologyProvider"
import { SessionProvider } from "./session"
import { loginUrl, toSession } from "./session-utils"

export function AuthGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const me = useMe()
  const location = useRouterState({ select: (s) => s.location.href })

  useEffect(() => {
    if (me.data === null) window.location.assign(loginUrl(location))
  }, [me.data, location])

  if (me.isPending || me.data === null) {
    return (
      <div className="space-y-3 p-8" aria-busy="true" aria-label={t("common.loading")}>
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }
  if (me.isError) return <QueryError error={me.error} onRetry={() => me.refetch()} />
  if (!me.data.active_business_id) return <Navigate to="/select-business" />
  return <SessionProvider value={toSession(me.data)}><TerminologyProvider>{children}</TerminologyProvider></SessionProvider>
}
