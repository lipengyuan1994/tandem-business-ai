import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import type { ActionFormProps } from "./types"

export function CustomerCareForm({ payload, pending, onSubmit, onCancel }: ActionFormProps) {
  const { t } = useTranslation()
  const [subject, setSubject] = useState(String(payload.subject ?? ""))
  const [body, setBody] = useState(String(payload.body ?? ""))
  const valid = !!subject.trim() && subject.length <= 160 && !!body.trim() && body.length <= 1000 && !/[\r\n<>]/.test(subject) && !subject.includes(String.fromCharCode(0)) && !/[\r<>]/.test(body) && !body.includes(String.fromCharCode(0))
  return <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (valid) onSubmit({ ...payload, subject: subject.trim(), body: body.trim() }) }}>
    <Label htmlFor="care-subject">{t("ai.care.subject")}</Label><Input id="care-subject" maxLength={160} value={subject} onChange={(e) => setSubject(e.target.value)} />
    <Label htmlFor="care-body">{t("ai.care.body")}</Label><Textarea id="care-body" rows={6} maxLength={1000} value={body} onChange={(e) => setBody(e.target.value)} />
    <p className="text-sm text-muted-foreground">{t("approvals.edit.note")}</p>
    <div className="flex gap-2"><Button type="submit" disabled={pending || !valid}>{t("approvals.edit.save")}</Button><Button type="button" variant="outline" onClick={onCancel}>{t("common.cancel")}</Button></div>
  </form>
}
