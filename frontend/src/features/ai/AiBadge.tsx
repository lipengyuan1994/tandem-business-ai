import { Sparkles } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Badge } from "@/components/ui/badge"

/** Marks a proposal the assistant wrote. Shows nothing for staff or rule proposals. */
export function AiBadge({ originKind }: { originKind: string }) {
  const { t } = useTranslation()
  if (originKind !== "assistant") return null
  return <Badge variant="secondary"><Sparkles aria-hidden="true" /> {t("ai.badge.proposed")}</Badge>
}
