import { QueryError } from "@/components/common/QueryError"
import { useNavigate } from "@tanstack/react-router"
import { useEffect } from "react"
import { useTranslation } from "react-i18next"
import { loginUrl } from "@/app/session-utils"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/common/EmptyState"
import { toastError } from "@/lib/errors"
import { useLogout, useMe, useSwitchBusiness } from "../hooks"

export function SelectBusinessPage() {
  const { t } = useTranslation()
  const me = useMe()
  const switchBusiness = useSwitchBusiness()
  const logout = useLogout()
  const navigate = useNavigate()

  useEffect(() => {
    if (me.data === null) window.location.assign(loginUrl("/select-business"))
  }, [me.data])

  if (me.error) return <QueryError error={me.error} onRetry={() => me.refetch()} />
  if (!me.data) return null
  const memberships = me.data.memberships
  return (
    <main className="mx-auto max-w-lg p-8">
      <h1 className="mb-1 text-2xl font-semibold">{t("identity.selectBusiness.title")}</h1>
      <p className="mb-6 text-sm text-muted-foreground">{t("identity.selectBusiness.subtitle", { name: me.data.user.display_name })}</p>
      {memberships.length === 0 ? (
        <EmptyState title={t("identity.selectBusiness.none")} description={t("identity.selectBusiness.noneHelp")}
          action={<Button variant="outline" onClick={() => logout.mutate()}>{t("identity.menu.logout")}</Button>} />
      ) : (
        <ul className="space-y-3">
          {memberships.map((m) => (
            <li key={m.business_id}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between gap-4">
                  <div>
                    <CardTitle className="text-base">{m.business_name}</CardTitle>
                    <p className="text-xs text-muted-foreground">{m.roles.map((r) => t(`roles.${r}`)).join(", ")}</p>
                  </div>
                  <Button
                    aria-label={t("identity.selectBusiness.openNamed", { name: m.business_name })}
                    disabled={switchBusiness.isPending}
                    onClick={() =>
                      switchBusiness.mutate(m.business_id, { onSuccess: () => navigate({ to: "/today" }), onError: (e) => toastError(e) })
                    }
                  >
                    {t("identity.selectBusiness.open")}
                  </Button>
                </CardHeader>
                <CardContent className="sr-only">{m.business_slug}</CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}
