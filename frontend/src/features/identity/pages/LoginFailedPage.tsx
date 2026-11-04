import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/common/EmptyState"

export function LoginFailedPage() {
  const { t } = useTranslation()
  return (
    <main className="mx-auto max-w-lg p-8">
      <EmptyState
        title={t("identity.loginFailed.title")}
        description={t("identity.loginFailed.help")}
        action={<Button onClick={() => window.location.assign("/api/v1/auth/login?return_to=%2Ftoday")}>{t("identity.loginFailed.retry")}</Button>}
      />
    </main>
  )
}
