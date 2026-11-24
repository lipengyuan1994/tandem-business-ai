import type { Schemas } from "@/api/types"

export type Rule = Schemas["RuleIn"]
export const hhmm = (v: string) => v.slice(0, 5)
export const byDayAndTime = (a: Rule, b: Rule) => a.weekday - b.weekday || hhmm(a.start_time).localeCompare(hhmm(b.start_time))

/** Problems the server would refuse, caught before saving (end after start, no overlaps on one day). */
export function rulesProblem(rules: Rule[]): { key: string; day: number } | null {
  const sorted = [...rules].sort(byDayAndTime)
  for (const [i, r] of sorted.entries()) {
    if (!r.start_time || !r.end_time || hhmm(r.end_time) <= hhmm(r.start_time)) return { key: "resources.hours.endBeforeStart", day: r.weekday }
    const prev = sorted[i - 1]
    if (prev && prev.weekday === r.weekday && hhmm(r.start_time) < hhmm(prev.end_time)) return { key: "resources.hours.overlap", day: r.weekday }
  }
  return null
}
