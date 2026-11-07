import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { LocalTime } from "@/components/common/LocalTime"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { minorToDecimal, parseMoneyInput } from "@/lib/money"
import { ApiProblem } from "@/lib/problem"
import { usePatchAgent, type Agent } from "./team-api"
import { teamReason, usdMicros } from "./team-text"
import { useClock } from "./useClock"

export function AgentCard({ agent }: { agent: Agent }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const [conflict, setConflict] = useState(false)
  const patch = usePatchAgent(agent.key)
  const [confirmOff, setConfirmOff] = useState(false)
  const save = (body: { enabled?: boolean; daily_cost_limit_usd?: string | null }) => {
    setConflict(false)
    patch.mutate({ ...body, version: agent.version }, { onError: (error) => {
      if (error instanceof ApiProblem && error.isConflict) setConflict(true)
    }, onSettled: () => setConfirmOff(false) })
  }
  const today = agent.today
  return <Card className="min-w-0">
    <CardHeader><CardTitle><a className="record-link" href={`/ai/team/${encodeURIComponent(agent.key)}`}>{agent.name}</a></CardTitle>
      <p className="text-sm text-muted-foreground">{agent.job}</p>
      <ul aria-label={t("ai.team.triggersLabel")} className="flex flex-wrap gap-1">{agent.triggers.map((trigger) => <li key={trigger} className="rounded-full border px-2 py-0.5 text-xs">{t(`ai.team.triggers.${trigger.replaceAll(".", "_")}`, { defaultValue: t("ai.team.otherTrigger") })}</li>)}</ul>
      <span className="status-badge w-fit" data-tone={agent.status === "on" ? "success" : agent.status === "unavailable" ? "failed" : "neutral"}>{t(`ai.team.status.${agent.status}`)}</span>
      {agent.reason ? <p className="text-sm">{teamReason(t, agent.reason)}</p> : null}
    </CardHeader>
    <CardContent className="space-y-4">
      {agent.switchable ? <PermissionGate permission="org.settings.write"><div className="flex items-center gap-2">
        <Switch id={`${agent.key}-enabled`} checked={agent.enabled} disabled={patch.isPending} onCheckedChange={(enabled) => enabled ? save({ enabled }) : setConfirmOff(true)} />
        <Label htmlFor={`${agent.key}-enabled`}>{t("ai.team.enable", { name: agent.name })}</Label>
      </div></PermissionGate> : null}
      {conflict ? <p role="alert">{t("ai.team.conflict")}</p> : patch.error && !(patch.error instanceof ApiProblem && patch.error.isConflict) ? <QueryError error={patch.error} onRetry={() => patch.reset()} /> : null}
      <section className="space-y-1"><h3 className="font-medium">{t("ai.team.autonomy")}</h3>
        {agent.autonomy.length ? <ul className="space-y-1 text-sm">{agent.autonomy.map((rule) => <li key={rule.action_type}><a className="record-link" href={`/ai/autonomy?action=${encodeURIComponent(rule.action_type)}`}>
          {t(`ai.autonomy.actions.${rule.action_type.replaceAll(".", "_")}`, { defaultValue: rule.action_type.replaceAll("_", " ") })}: {t(`ai.autonomy.levels.${rule.active ? rule.level : "off"}.label`)}{rule.shadow ? ` · ${t("ai.team.shadow")}` : ""} · {t("ai.team.perDay", { count: rule.daily_cap })}
        </a></li>)}</ul> : <p className="text-sm text-muted-foreground">{t("ai.team.noActions")}</p>}
      </section>
      <section className="space-y-1"><h3 className="font-medium">{t("ai.team.today")}</h3><p className="text-sm">{t("ai.team.work", { runs: today.runs, proposals: today.proposals_created })}</p><p className="text-sm">{t("ai.team.done", { people: today.actions_executed_by_people, rules: today.actions_executed_by_rules, handed: today.handed_to_person })}</p></section>
      <p className="text-sm">{t("ai.team.cost", { cost: usdMicros(agent.cost_today_micros), limit: agent.daily_cost_limit_usd === null ? t("ai.team.noLimit") : usdMicros(Number(agent.daily_cost_limit_usd) * 1_000_000) })}</p>
      <PermissionGate permission="org.settings.write"><AgentLimit key={`${agent.version}:${agent.daily_cost_limit_usd}`} agent={agent} pending={patch.isPending} save={(value) => save({ daily_cost_limit_usd: value })} /></PermissionGate>
      <p className="text-sm"><a className="record-link" href={`/ai/work-queue?agent=${encodeURIComponent(agent.key)}`}>{t("ai.team.waiting", { count: agent.waiting.count })}</a>{agent.waiting.oldest_at ? <> · <WaitingAge since={agent.waiting.oldest_at} /></> : null}</p>
      <p className="text-sm text-muted-foreground">{t("ai.team.lastActivity")} {agent.last_activity_at ? <LocalTime iso={agent.last_activity_at} timeZone={businessTimeZone} pattern="d MMM yyyy HH:mm" /> : t("ai.team.noneYet")}</p>
      <ConfirmDialog open={confirmOff} onOpenChange={setConfirmOff} title={t("ai.team.turnOff", { name: agent.name })} description={t("ai.team.turnOffHelp")} confirmLabel={t("ai.team.confirmOff")} destructive pending={patch.isPending} onConfirm={() => save({ enabled: false })} />
    </CardContent>
  </Card>
}

function AgentLimit({ agent, pending, save }: { agent: Agent; pending: boolean; save: (value: string | null) => void }) {
  const { t } = useTranslation()
  // Remove insignificant backend precision without rounding a sub-cent ceiling.
  const [value, setValue] = useState(() => {
    const original = agent.daily_cost_limit_usd ?? ""
    const parsed = parseMoneyInput(original.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, ""), "USD")
    return parsed.ok ? minorToDecimal(parsed.minor, "USD") : original
  })
  const parsed = parseMoneyInput(value, "USD")
  const valid = !value.trim() || parsed.ok
  return <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (valid) save(!value.trim() ? null : parsed.ok ? minorToDecimal(parsed.minor, "USD") : null) }}>
    <Label htmlFor={`${agent.key}-limit`}>{t("ai.team.limit")}</Label>
    <div className="flex items-center gap-2"><Input id={`${agent.key}-limit`} inputMode="decimal" value={value} disabled={pending} aria-invalid={!valid} aria-describedby={`${agent.key}-limit-help`} onChange={(e) => setValue(e.target.value)} /><Button type="submit" size="sm" variant="outline" disabled={pending || !valid}>{t("common.save")}</Button></div>
    <p id={`${agent.key}-limit-help`} className="text-xs text-muted-foreground">{t("ai.team.limitHelp")}</p>{!valid ? <p role="alert" className="text-sm">{t("ai.team.limitInvalid")}</p> : null}
  </form>
}

function WaitingAge({ since }: { since: string }) {
  const { t } = useTranslation()
  const now = useClock()
  const minutes = Math.max(0, Math.floor((now - Date.parse(since)) / 60000))
  return <span>{t("ai.team.oldest", { age: minutes < 60 ? `${minutes}m` : minutes < 1440 ? `${Math.floor(minutes / 60)}h` : `${Math.floor(minutes / 1440)}d` })}</span>
}
