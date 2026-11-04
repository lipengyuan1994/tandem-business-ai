import type { ErrorComponentProps } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"
import { QueryError } from "@/components/common/QueryError"
import { EmptyState } from "@/components/common/EmptyState"

export function RouteError({ error, reset }: ErrorComponentProps) {
  return <QueryError error={error instanceof Error ? error : new Error(String(error))} onRetry={reset ?? (() => window.location.reload())} />
}

export function NotFound() {
  const { t } = useTranslation()
  return <EmptyState title={t("notFound.title")} action={<a className="text-sm underline" href="/today">{t("notFound.back")}</a>} />
}
