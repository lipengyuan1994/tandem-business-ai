import type { TFunction } from "i18next"
import { formatMoney } from "@/lib/money"

export function usdMicros(micros: number) {
  return formatMoney({ amount_minor: micros / 10_000, currency: "USD" })
}

export function teamReason(t: TFunction, reason: string) {
  if (reason.startsWith("customer_care.")) return t(`ai.care.reasons.${reason.slice("customer_care.".length)}`, { defaultValue: t("ai.care.unknownReason") })
  if (/coming soon|no triggers|coming_soon/i.test(reason)) return t("ai.team.comingSoon")
  if (/no local route|needs? a local model/i.test(reason)) return t("ai.team.needsLocal")
  // The backend also supplies readable explanations (for example a missing local route).
  return t(`ai.team.reasons.${reason.replaceAll(".", "_")}`, {
    defaultValue: t(`ai.activity.reasons.${reason}`, { defaultValue: /^[a-z_]+(?:\.[a-z_]+)+$/.test(reason) ? t("ai.team.reasonUnknown") : reason }),
  })
}
