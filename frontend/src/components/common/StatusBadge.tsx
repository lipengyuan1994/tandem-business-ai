import { useTranslation } from "react-i18next"
import { cn } from "@/lib/utils"

type Tone = "pending" | "approved" | "success" | "failed" | "neutral"

// One mapping for every status in the app, so colours mean the same thing everywhere (09 design system).
const TONES: Record<string, Tone> = {
  pending_review: "pending", held: "pending", pending: "pending", waiting_on_staff: "pending", executing: "approved",
  approved: "approved", confirmed: "approved", open: "approved", waiting_on_customer: "neutral",
  succeeded: "success", completed: "success", delivered: "success", simulated: "success",
  failed: "failed", rejected: "failed", unknown: "neutral", active: "success", inactive: "neutral", no_show: "failed",
  expired: "neutral", cancelled: "neutral", closed: "neutral",
  draft: "neutral", in_review: "pending", sending: "approved", sent: "success",
  accepted: "success", declined: "failed", fulfilled: "success", issued: "approved", paid: "success", void: "neutral",
}


export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const { t } = useTranslation()
  const tone = TONES[status] ?? "neutral"
  const label = t(`status.${status}`, { defaultValue: status.replaceAll("_", " ") })
  return (
    <span data-tone={tone} className={cn("status-badge", className)}>
      {label}
    </span>
  )
}
