import { Link } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"
import type { Schemas } from "@/api/types"

export function ProposalFeedback({ proposal }: { proposal: Schemas["StaffProposalOut"] }) {
  const { t } = useTranslation()
  return <div className="space-y-2 rounded-md border p-3"><p role="status">{t("procurement.proposed")}</p><Link className="record-link" to="/approvals/$proposalId" params={{ proposalId: proposal.id }}>{t("procurement.review")}</Link></div>
}
