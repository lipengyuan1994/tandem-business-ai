import type { ReactNode } from "react"
import { ANONYMOUS, SessionContext, type SessionState } from "./session-context"

export function SessionProvider({ value, children }: { value?: SessionState; children: ReactNode }) {
  return <SessionContext.Provider value={value ?? ANONYMOUS}>{children}</SessionContext.Provider>
}
