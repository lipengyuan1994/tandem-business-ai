import { formatQuantity } from "@/lib/quantity"
import { useTranslation } from "react-i18next"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { toastError } from "@/lib/errors"
import { toast } from "sonner"
import type { ReorderSuggestion } from "@/api/types"
import { useCreateReorderDraft } from "../hooks"

/** Deterministic reorder suggestions with their explanation; "Create draft" records a draft, never an order. */
export function SuggestionsPanel({ suggestions }: { suggestions: ReorderSuggestion[] | undefined }) {
  const { t } = useTranslation()
  const create = useCreateReorderDraft()
  if (!suggestions?.length) return null
  const createDraft = (items: ReorderSuggestion[]) =>
    create.mutate(items.map((s) => ({ item_id: s.item_id, quantity: s.suggested, reason_text: s.explanation })), {
      onSuccess: () => toast.success(t("inventory.suggestions.created")),
      onError: (e) => toastError(e),
    })
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between">
        <CardTitle className="text-sm">{t("inventory.suggestions.title")}</CardTitle>
        <PermissionGate permission="inventory.reorder.write">
          <Button size="sm" disabled={create.isPending} onClick={() => createDraft(suggestions)}>{t("inventory.suggestions.createAll")}</Button>
        </PermissionGate>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2 text-sm">
          {suggestions.map((s) => (
            <li key={s.item_id}>
              <span className="font-medium">{s.item_name}</span>: {t("inventory.suggestions.line", { n: formatQuantity(s.suggested), available: formatQuantity(s.available) })}
              <p className="break-words text-sm text-muted-foreground">{s.explanation}</p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
