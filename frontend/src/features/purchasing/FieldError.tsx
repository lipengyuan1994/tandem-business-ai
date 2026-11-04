import { useTranslation } from "react-i18next"
import { fieldInvalid } from "./validation"

export function FieldError({ error, field }: { error: unknown; field: string }) {
  const { t } = useTranslation()
  return fieldInvalid(error, field) ? <p className="text-sm text-destructive" role="alert">{t("purchasing.invalidField")}</p> : null
}
