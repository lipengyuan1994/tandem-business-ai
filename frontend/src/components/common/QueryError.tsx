import { useTranslation } from "react-i18next"
import { EmptyState } from "@/components/common/EmptyState"
import { Button } from "@/components/ui/button"
import { describeError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"

/** A failed request, with a retry. A 404 on a record page is not a failure to retry: pass `notFound` to say so. */
export function QueryError({ error, onRetry, notFound }: { error: Error; onRetry: () => unknown; notFound?: string }) {
  const { t } = useTranslation()
  if (notFound && error instanceof ApiProblem && error.status === 404) return <EmptyState title={notFound} />
  return <div role="alert" className="query-error">
    <p className="font-medium">{t("common.errorTitle")}</p>
    <p className="text-sm break-words">{describeError(error)}</p>
    <Button type="button" variant="outline" onClick={() => void onRetry()}>{t("common.retry")}</Button>
  </div>
}
