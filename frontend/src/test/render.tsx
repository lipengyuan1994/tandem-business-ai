import { render } from "@testing-library/react"
import type { ReactElement } from "react"
import { SessionProvider } from "@/app/session"
import type { SessionState } from "@/app/session-context"

export function renderWithSession(ui: ReactElement, permissions: string[] = []) {
  const session: SessionState = {
    status: "authenticated",
    userId: "00000000-0000-7000-8000-000000000001",
    businessId: "00000000-0000-7000-8000-000000000002",
    displayName: "Morgan Example",
    businessName: "Example Cycle Works",
    businessTimeZone: "America/Denver",
    permissions: new Set(permissions),
  }
  return render(<SessionProvider value={session}>{ui}</SessionProvider>)
}
