import { useTranslation } from "react-i18next"
import type { Schemas } from "@/api/types"

export function EvidenceList({ evidence }: { evidence: Schemas["Evidence"][] }) {
  const { t } = useTranslation()
  if (evidence.length === 0) return <p className="text-sm text-muted-foreground">{t("approvals.noEvidence")}</p>
  return (
    <ul className="space-y-2">
      {evidence.map((e, i) => (
        <li key={i} className="rounded-md border p-2 text-sm [overflow-wrap:anywhere]">
          <span className="mr-2 text-xs uppercase text-muted-foreground">{t(`approvals.evidenceKinds.${e.kind}`)}</span>
          <q className="italic">{e.excerpt}</q>
        </li>
      ))}
    </ul>
  )
}
