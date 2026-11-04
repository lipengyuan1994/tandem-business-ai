import type { ReactNode } from "react"
import { Inbox } from "lucide-react"

export function EmptyState({ title, description, action, icon }: { title: string; description?: string; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div role="status" className="empty-state">
      <div className="mb-3 text-muted-foreground" aria-hidden="true">{icon ?? <Inbox className="size-8" />}</div>
      <p className="font-medium">{title}</p>
      {description ? <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  )
}
