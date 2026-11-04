import { useMemo, type ReactNode } from "react"
import { I18nextProvider } from "react-i18next"
import { Skeleton } from "@/components/ui/skeleton"
import { useSettings } from "@/features/org/hooks"
import i18n from "./i18n"
import { termVariables } from "./terminology"

/** Scoped to the active business; replaces variables when settings change, never the shared translations. */
export function TerminologyProvider({ children }: { children: ReactNode }) {
  const settings = useSettings()
  const terms = settings.data?.settings.terminology
  const scoped = useMemo(() => i18n.cloneInstance({
    interpolation: { escapeValue: false, defaultVariables: termVariables(terms) },
  }), [terms])
  if (settings.isPending) return <Skeleton className="h-64 w-full" />
  return <I18nextProvider i18n={scoped}>{children}</I18nextProvider>
}
