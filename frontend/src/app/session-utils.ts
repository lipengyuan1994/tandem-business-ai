import type { Me } from "@/api/types"
import type { SessionState } from "./session-context"

export function toSession(me: Me): SessionState {
  const active = me.memberships.find((m) => m.business_id === me.active_business_id)
  return {
    status: "authenticated",
    userId: me.user.id,
    displayName: me.user.display_name,
    businessId: me.active_business_id ?? null,
    businessName: active?.business_name ?? null,
    businessTimeZone: me.active_business_timezone ?? "UTC",
    permissions: new Set(me.permissions),
  }
}

export function loginUrl(returnTo: string): string {
  return `/api/v1/auth/login?return_to=${encodeURIComponent(returnTo)}`
}
