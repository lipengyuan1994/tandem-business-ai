import type { ReactNode } from "react"
import { useSession } from "@/app/session-context"
import { hasPermission } from "@/lib/permissions"

export function PermissionGate({ permission, children, fallback = null }: { permission: string; children: ReactNode; fallback?: ReactNode }) {
  const { permissions } = useSession()
  return <>{hasPermission(permissions, permission) ? children : fallback}</>
}
