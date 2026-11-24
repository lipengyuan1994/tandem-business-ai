import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { Schemas } from "@/api/types"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { formatQuantity as qty, quantityInput } from "@/lib/quantity"
import { toastError } from "@/lib/errors"
import { useSaveReorderRule } from "../hooks"

type Rule = Schemas["ReorderRuleOut"]
const num = (s: string) => (s.trim() === "" ? NaN : Number(s))

function RuleForm({ itemId, rule, onDone }: { itemId: string; rule: Rule | null; onDone: () => void }) {
  const { t } = useTranslation()
  const save = useSaveReorderRule(itemId)
  const [point, setPoint] = useState(rule ? quantityInput(rule.reorder_point) : "")
  const [target, setTarget] = useState(rule ? quantityInput(rule.target_level) : "")
  const [days, setDays] = useState(String(rule?.lead_time_days ?? 0))
  const [supplier, setSupplier] = useState(rule?.supplier_hint ?? "")
  const p = num(point), tl = num(target), d = num(days)
  const problem = !(p >= 0) || !(tl > 0) || !Number.isInteger(d) || d < 0 || d > 365 ? "inventory.ruleForm.invalid" : tl <= p ? "inventory.ruleForm.targetAbovePoint" : null

  return (
    <form className="space-y-3 text-sm" aria-label={t("inventory.rule")} onSubmit={(e) => {
      e.preventDefault()
      if (problem) return
      save.mutate({ reorder_point: point.trim(), target_level: target.trim(), lead_time_days: d, supplier_hint: supplier.trim() || null }, {
        onSuccess: () => { toast.success(t("inventory.ruleForm.saved")); onDone() },
        onError: (err) => toastError(err),
      })
    }}>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1"><Label htmlFor="rule-point">{t("inventory.ruleForm.point")}</Label><Input id="rule-point" inputMode="decimal" value={point} onChange={(e) => setPoint(e.target.value)} /></div>
        <div className="space-y-1"><Label htmlFor="rule-target">{t("inventory.ruleForm.target")}</Label><Input id="rule-target" inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)} /></div>
      </div>
      <div className="space-y-1"><Label htmlFor="rule-days">{t("inventory.ruleForm.leadTime")}</Label><Input id="rule-days" inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} /></div>
      <div className="space-y-1"><Label htmlFor="rule-supplier">{t("inventory.ruleForm.supplierHint")}</Label><Input id="rule-supplier" maxLength={200} value={supplier} onChange={(e) => setSupplier(e.target.value)} /></div>
      {problem && (point || target) ? <p role="alert" className="text-destructive">{t(problem)}</p> : null}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={save.isPending || !!problem}>{t("common.save")}</Button>
        <Button type="button" size="sm" variant="outline" onClick={onDone}>{t("common.cancel")}</Button>
      </div>
    </form>
  )
}

/** The item's reorder rule: below the reorder point, suggestions propose ordering back up to the target level. */
export function ReorderRuleCard({ itemId, rule }: { itemId: string; rule: Rule | null }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const canEdit = permissions.has("inventory.reorder.write")
  const [editing, setEditing] = useState(false)
  if (!rule && !canEdit) return null
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-sm">{t("inventory.rule")}</CardTitle>
        {canEdit && !editing ? <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>{rule ? t("inventory.ruleForm.edit") : t("inventory.ruleForm.set")}</Button> : null}
      </CardHeader>
      <CardContent className="text-sm">
        {editing ? <RuleForm itemId={itemId} rule={rule} onDone={() => setEditing(false)} />
          : rule ? <>{t("inventory.ruleLine", { point: qty(rule.reorder_point), target: qty(rule.target_level), days: rule.lead_time_days })}{rule.supplier_hint ? <p className="text-muted-foreground">{t("inventory.ruleForm.supplierLine", { supplier: rule.supplier_hint })}</p> : null}</>
          : <p className="text-muted-foreground">{t("inventory.ruleForm.none")}</p>}
      </CardContent>
    </Card>
  )
}
