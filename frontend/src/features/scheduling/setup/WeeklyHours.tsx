import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { Resource } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { toastError } from "@/lib/errors"
import { useReplaceAvailability } from "../hooks"
import { type Rule, byDayAndTime, hhmm, rulesProblem } from "./rules"

const DAYS = [0, 1, 2, 3, 4, 5, 6] as const

/**
 * When this resource can be booked, per weekday. Several intervals per day are allowed (a lunch break); a day with
 * none means not bookable. Slots also need the location's opening hours, so both must cover a time.
 */
export function WeeklyHours({ resource, canEdit }: { resource: Resource; canEdit: boolean }) {
  const { t } = useTranslation()
  const replace = useReplaceAvailability(resource.id)
  const [draft, setDraft] = useState<Rule[]>(() => resource.rules.map((r) => ({ ...r, start_time: hhmm(r.start_time), end_time: hhmm(r.end_time) })))
  const problem = rulesProblem(draft)
  const day = (d: number) => t(`weekdays.${d}`)

  const change = (index: number, patch: Partial<Rule>) => setDraft(draft.map((r, i) => (i === index ? { ...r, ...patch } : r)))
  const remove = (index: number) => setDraft(draft.filter((_, i) => i !== index))
  const add = (weekday: number) => {
    const last = draft.filter((r) => r.weekday === weekday).sort(byDayAndTime).at(-1)
    const start = last ? hhmm(last.end_time) : "09:00"
    const hour = Math.min(Number(start.slice(0, 2)) + 1, 23)
    const end = last ? (start < "17:00" ? "18:00" : hour === 23 ? "23:59" : `${String(hour).padStart(2, "0")}${start.slice(2)}`) : "17:00"
    setDraft([...draft, { weekday, start_time: start, end_time: end }])
  }
  const copyMondayToWeekdays = () => {
    const monday = draft.filter((r) => r.weekday === 0)
    setDraft([...draft.filter((r) => r.weekday === 0 || r.weekday > 4), ...[1, 2, 3, 4].flatMap((d) => monday.map((r) => ({ ...r, weekday: d })))])
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">{t("resources.hours.hint")}</p>
      <ul className="divide-y rounded-md border">
        {DAYS.map((d) => {
          const rows = draft.map((r, i) => ({ r, i })).filter(({ r }) => r.weekday === d).sort((a, b) => byDayAndTime(a.r, b.r))
          return (
            <li key={d} className="flex flex-wrap items-start gap-3 p-2">
              <span className="w-28 pt-2 text-sm font-medium">{day(d)}</span>
              <div className="flex-1 space-y-2">
                {rows.length === 0 ? <p className="pt-2 text-sm text-muted-foreground">{t("resources.hours.notBookable")}</p> : rows.map(({ r, i }, n) => (
                  <div key={i} className="flex flex-wrap items-center gap-2">
                    <Input type="time" className="w-32" aria-label={t("resources.hours.startFor", { day: day(d), n: n + 1 })} disabled={!canEdit} value={r.start_time} onChange={(e) => change(i, { start_time: e.target.value })} />
                    <span aria-hidden>–</span>
                    <Input type="time" className="w-32" aria-label={t("resources.hours.endFor", { day: day(d), n: n + 1 })} disabled={!canEdit} value={r.end_time} onChange={(e) => change(i, { end_time: e.target.value })} />
                    {canEdit ? <Button type="button" size="sm" variant="ghost" onClick={() => remove(i)} aria-label={t("resources.hours.removeFor", { day: day(d), n: n + 1 })}>{t("resources.hours.remove")}</Button> : null}
                  </div>
                ))}
              </div>
              {canEdit ? <Button type="button" size="sm" variant="ghost" onClick={() => add(d)} aria-label={t("resources.hours.addFor", { day: day(d) })}>{t("resources.hours.add")}</Button> : null}
            </li>
          )
        })}
      </ul>
      {problem ? <p role="alert" className="text-sm text-destructive">{t(problem.key, { day: day(problem.day) })}</p> : null}
      {canEdit ? (
        <div className="flex flex-wrap gap-2">
          <Button disabled={replace.isPending || !!problem} onClick={() => replace.mutate([...draft].sort(byDayAndTime), { onSuccess: () => toast.success(t("resources.hours.saved")), onError: (e) => toastError(e) })}>
            {t("resources.hours.save")}
          </Button>
          <Button type="button" variant="outline" disabled={!draft.some((r) => r.weekday === 0)} onClick={copyMondayToWeekdays}>{t("resources.hours.copyMonday")}</Button>
        </div>
      ) : null}
    </div>
  )
}
