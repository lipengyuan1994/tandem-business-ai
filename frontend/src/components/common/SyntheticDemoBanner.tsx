import { useTranslation } from "react-i18next"
import { useSettings } from "@/features/org/hooks"

/** The backend owns the required statement; do not reconstruct it from a business name. */
export function SyntheticDemoBanner() {
  const { t } = useTranslation()
  const business = useSettings()
  const text = business.data?.synthetic_demo_banner
  return text ? <aside role="note" aria-label={t("dental.demoNotice")} className="mb-4 rounded-md border border-amber-500/60 bg-amber-50 p-3 text-sm text-amber-950">{text}</aside> : null
}
