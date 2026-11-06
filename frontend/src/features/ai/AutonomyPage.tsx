import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { EmptyState } from "@/components/common/EmptyState"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { ApiProblem } from "@/lib/problem"
import { toastError } from "@/lib/errors"
import { useAutonomyPause, useAutonomyRules, useSaveAutonomyRule, type AutonomyRule, type RuleInput } from "./autonomy-api"

import { autonomyRoute } from "./routes"
import { SendDelaySetting } from "./SendDelaySetting"

const LEVELS = ["off", "draft", "ask", "act_notify", "act"] as const

export function AutonomyPage() {
  const { t } = useTranslation()
  return <PermissionGate permission="org.settings.write" fallback={<EmptyState title={t("ai.autonomy.noAccess")} />}><AutonomySettings /></PermissionGate>
}

function AutonomySettings() {
  const { t } = useTranslation()
  const { action } = autonomyRoute.useSearch()
  const rules = useAutonomyRules()
  const pause = useAutonomyPause()
  const [confirmPause, setConfirmPause] = useState(false)
  if (rules.error) return <QueryError error={rules.error} onRetry={() => rules.refetch()} />
  if (!rules.data) return <Skeleton className="h-64 w-full" />
  const { paused, items } = rules.data
  return <div className="max-w-3xl space-y-6">
    <PageHeader title={t("ai.autonomy.title")} description={t("ai.autonomy.description")} />
    {paused ? <p role="status" className="rounded-md border border-destructive p-4 font-medium">{t("ai.autonomy.paused")}</p> : null}
    <Button variant={paused ? "outline" : "destructive"} disabled={pause.isPending} onClick={() => paused ? pause.mutate(false, { onError: (error) => toastError(error) }) : setConfirmPause(true)}>{t(paused ? "ai.autonomy.resume" : "ai.autonomy.pause")}</Button>
    <SendDelaySetting />
    {action ? <a href="/ai/autonomy" className="record-link">{t("ai.team.allActions")}</a> : null}
    {items.filter((rule) => !action || rule.action_type === action).map((rule) => <RuleCard key={`${rule.action_type}:${rule.version}`} rule={rule} onReload={() => void rules.refetch()} />)}
    <ConfirmDialog open={confirmPause} onOpenChange={setConfirmPause} title={t("ai.autonomy.pause")} description={t("ai.autonomy.pauseHelp")} confirmLabel={t("ai.autonomy.confirmPause")} destructive pending={pause.isPending} onConfirm={() => pause.mutate(true, { onSuccess: () => setConfirmPause(false), onError: (error) => toastError(error) })} />
  </div>
}

function RuleCard({ rule, onReload }: { rule: AutonomyRule; onReload: () => void }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const save = useSaveAutonomyRule()
  const [level, setLevel] = useState(rule.level)
  const [shadow, setShadow] = useState(rule.shadow)
  const [active, setActive] = useState(rule.active)
  const [cap, setCap] = useState(String(rule.daily_cap))
  const [conflict, setConflict] = useState(false)
  const canSave = permissions.has(rule.action_type === "conversations.send_reply" ? "conversations.proposal.approve" : "inventory.proposal.approve")
  const capValue = /^\d+$/.test(cap) && Number(cap) <= 2147483647 ? Number(cap) : undefined
  const prefix = rule.action_type
  return <Card>
    <CardHeader><CardTitle>{t(`ai.autonomy.actions.${rule.action_type.replaceAll(".", "_")}`, { defaultValue: rule.action_type })}</CardTitle></CardHeader>
    <CardContent>
      {rule.action_type === "conversations.send_reply" ? <section className="mb-4 space-y-2 text-sm"><h3 className="font-medium">{t("ai.autonomy.replyFallbacks")}</h3><ul className="list-disc pl-5">{["not_intake", "safety_unchecked", "ungrounded", "contradicted", "grounding_unavailable", "human_mode", "bundled_action", "stale_reply", "conversation_closed"].map((reason) => <li key={reason}>{t(`ai.activity.reasons.${reason}`)}</li>)}</ul></section> : null}
      <form className="space-y-5" onSubmit={(e) => {
        e.preventDefault()
        if (capValue === undefined || !canSave || conflict) return
        save.mutate({ action_type: rule.action_type, version: rule.version, level, shadow, daily_cap: capValue, active }, { onError: (err) => err instanceof ApiProblem && err.isConflict ? setConflict(true) : toastError(err) })
      }}>
        <fieldset disabled={!canSave || save.isPending} className="space-y-2">
          <legend className="mb-2 font-medium">{t("ai.autonomy.level")}</legend>
          {LEVELS.map((value) => <div key={value}>
            <label className="flex items-center gap-2"><input type="radio" name={`${prefix}-level`} value={value} checked={level === value} disabled={value === "draft"} aria-describedby={`${prefix}-${value}-help`} onChange={() => setLevel(value as RuleInput["level"])} />{t(`ai.autonomy.levels.${value}.label`)}</label>
            <p id={`${prefix}-${value}-help`} className="ml-6 text-sm text-muted-foreground">{t(`ai.autonomy.levels.${value}.help`)}</p>
          </div>)}
        </fieldset>
        <div className="space-y-2"><div className="flex items-center gap-2"><Switch id={`${prefix}-shadow`} checked={shadow} onCheckedChange={setShadow} disabled={!canSave || save.isPending} aria-describedby={`${prefix}-shadow-help`} /><Label htmlFor={`${prefix}-shadow`}>{t("ai.autonomy.shadow")}</Label></div>
          <p id={`${prefix}-shadow-help`} className="text-sm text-muted-foreground">{t("ai.autonomy.shadowHelp")}</p>
          {rule.version === 0 ? <p className="text-sm">{t("ai.autonomy.newShadow")}</p> : null}
        </div>
        <div className="space-y-2"><Label htmlFor={`${prefix}-cap`}>{t("ai.autonomy.cap")}</Label><Input id={`${prefix}-cap`} type="text" inputMode="numeric" value={cap} onChange={(e) => setCap(e.target.value)} disabled={!canSave || save.isPending} aria-invalid={capValue === undefined} />
          <p className="text-sm text-muted-foreground">{t("ai.autonomy.capHelp")}</p>{capValue === undefined ? <p role="alert">{t("ai.autonomy.capInvalid")}</p> : null}
        </div>
        <div className="flex items-center gap-2"><Switch id={`${prefix}-active`} checked={active} onCheckedChange={setActive} disabled={!canSave || save.isPending} /><Label htmlFor={`${prefix}-active`}>{t("ai.autonomy.active")}</Label></div>
        <section className="space-y-1"><h3 className="font-medium">{t("ai.autonomy.summary")}</h3><p className="text-sm">{t("ai.autonomy.counts", rule.summary)}</p></section>
        {!canSave ? <p>{t(rule.action_type === "conversations.send_reply" ? "ai.autonomy.cannotGrantReply" : "ai.autonomy.cannotGrant")}</p> : null}
        <Button type="submit" disabled={!canSave || save.isPending || capValue === undefined || conflict}>{t("common.save")}</Button>
        <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); onReload() }} />
      </form>
    </CardContent>
  </Card>
}
