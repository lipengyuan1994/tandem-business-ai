import { useTranslation } from "react-i18next"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { disabledText, useAiAvailability } from "./text"

/** Says why AI is off (not set up, privacy policy, daily limit); a soft note from 80 % of the limit. Nothing when AI is fine. */
export function AiDisabledBanner({ className }: { className?: string }) {
  const { t } = useTranslation()
  const { status } = useAiAvailability()
  if (!status) return null
  if (!status.enabled) {
    return <Alert role="status" className={className}><AlertDescription>{disabledText(t, status.reason)}</AlertDescription></Alert>
  }
  if (status.budget_percent >= 80) {
    return <Alert role="status" className={className}><AlertDescription>{t("ai.disabled.nearLimit", { percent: status.budget_percent })}</AlertDescription></Alert>
  }
  return null
}
