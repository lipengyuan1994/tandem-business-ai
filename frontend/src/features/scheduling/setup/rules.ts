import type { Schemas } from "@/api/types"

export type Rule = Schemas["RuleIn"]
export const hhmm = (v: string) => v.slice(0, 5)
export const byDayAndTime = (a: Rule, b: Rule) => a.weekday - b.weekday || hhmm(a.start_time).localeCompare(hhmm(b.start_time))

/** Whether two rules can apply on the same date: their valid_from/valid_to windows (open-ended when unset) intersect. */
const sameSeason = (a: Rule, b: Rule) =>
  (a.valid_from ?? "") <= (b.valid_to ?? "9999-12-31") && (b.valid_from ?? "") <= (a.valid_to ?? "9999-12-31")

/** Problems the server would refuse, caught before saving (end after start, no overlaps on one day). */
export function rulesProblem(rules: Rule[]): { key: string; day: number } | null {
  const sorted = [...rules].sort(byDayAndTime)
  for (const [i, r] of sorted.entries()) {
    if (!r.start_time || !r.end_time || hhmm(r.end_time) <= hhmm(r.start_time)) return { key: "resources.hours.endBeforeStart", day: r.weekday }
    const clash = sorted.slice(0, i).some((p) => p.weekday === r.weekday && hhmm(r.start_time) < hhmm(p.end_time) && sameSeason(p, r))
    if (clash) return { key: "resources.hours.overlap", day: r.weekday }
  }
  return null
}
