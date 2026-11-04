// Session and permissions for the UI, filled from /api/v1/auth/me by AuthGate.
import { createContext, useContext } from "react"
import type { PermissionSet } from "@/lib/permissions"

export interface SessionState {
  status: "authenticated" | "anonymous"
  userId: string | null
  displayName: string | null
  businessId: string | null
  businessName: string | null
  businessTimeZone: string
  permissions: PermissionSet
}

export const ANONYMOUS: SessionState = {
  status: "anonymous",
  userId: null,
  displayName: null,
  businessId: null,
  businessName: null,
  businessTimeZone: "UTC",
  permissions: new Set(),
}

export const SessionContext = createContext<SessionState>(ANONYMOUS)

export function useSession(): SessionState {
  return useContext(SessionContext)
}
