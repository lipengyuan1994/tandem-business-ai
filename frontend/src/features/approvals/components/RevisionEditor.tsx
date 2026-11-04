import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"

interface JsonSchema {
  properties?: Record<string, { type?: string; title?: string; description?: string }>
}

/** Generic edit form built from the action type's JSON schema (flat fields); anything nested falls back to JSON.
 *  Action types listed in ./forms (booking slot picker, reply text, stock adjustment) use their own form instead. */
export function RevisionEditor({ schema, payload, pending, onSubmit, onCancel }: {
  schema: JsonSchema | undefined
  payload: Record<string, unknown>
  pending: boolean
  onSubmit: (payload: Record<string, unknown>) => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<Record<string, unknown>>(payload)
  const [jsonText, setJsonText] = useState(JSON.stringify(payload, null, 2))
  const props = schema?.properties ?? {}
  const flat = Object.entries(props).every(([, p]) => ["string", "integer", "number", "boolean"].includes(p.type ?? ""))
  const [jsonError, setJsonError] = useState<string | null>(null)

  const submit = () => {
    if (flat) return onSubmit(draft)
    try {
      onSubmit(JSON.parse(jsonText) as Record<string, unknown>)
    } catch {
      setJsonError(t("approvals.edit.invalidJson"))
    }
  }

  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit() }}>
      {flat ? (
        Object.entries(props).map(([key, p]) => (
          <div key={key} className={p.type === "boolean" ? "flex items-center gap-2" : "space-y-1"}>
            {p.type === "boolean" ? (
              <>
                <Checkbox id={`edit-${key}`} checked={!!draft[key]} onCheckedChange={(c) => setDraft({ ...draft, [key]: c === true })} />
                <Label htmlFor={`edit-${key}`}>{p.title ?? key}</Label>
              </>
            ) : (
              <>
                <Label htmlFor={`edit-${key}`}>{p.title ?? key}</Label>
                <Input id={`edit-${key}`} type={p.type === "string" ? "text" : "number"} value={String(draft[key] ?? "")}
                  onChange={(e) => setDraft({ ...draft, [key]: p.type === "string" ? e.target.value : Number(e.target.value) })} />
              </>
            )}
          </div>
        ))
      ) : (
        <div className="space-y-1">
          <Label htmlFor="edit-json">{t("approvals.edit.json")}</Label>
          <Textarea id="edit-json" rows={10} className="font-mono text-xs" value={jsonText} onChange={(e) => setJsonText(e.target.value)} aria-invalid={!!jsonError} />
          {jsonError ? <p role="alert" className="text-sm text-destructive">{jsonError}</p> : null}
        </div>
      )}
      <p className="text-xs text-muted-foreground">{t("approvals.edit.note")}</p>
      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>{t("approvals.edit.save")}</Button>
        <Button type="button" variant="outline" onClick={onCancel}>{t("common.cancel")}</Button>
      </div>
    </form>
  )
}
