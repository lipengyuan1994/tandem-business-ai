import { useTranslation } from "react-i18next"
import { periodLabel, type Period } from "../model"

const FREE_TEXT = "free_text"

export function usePeriodOptions(): { value: Period; label: string }[] {
  const { t } = useTranslation()
  return (["day", "week", "month"] as const).map((p) => ({ value: p, label: t(`reports.periods.${p}`) }))
}

/** Display label for a report row: period keys become dates, free-text and item-kind keys are translated. */
export function useRowLabel() {
  const { t, i18n } = useTranslation()
  return (groupBy: string, key: string, label: string, period?: Period): string => {
    if (groupBy === "period" && period) {
      const text = periodLabel(key, period, i18n.language)
      return period === "week" ? t("reports.weekOf", { date: text }) : text
    }
    if (groupBy === "kind") return t(`reports.kinds.${key}`, { defaultValue: key })
    if (key === FREE_TEXT) return t("reports.freeText")
    return label || t("reports.unnamed")
  }
}
