import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import type { ActionFormProps } from "./types"

/** conversations.send_reply: edit the text only. */
export function ReplyForm({ payload, pending, onSubmit, onCancel }: ActionFormProps) {
  const { t } = useTranslation()
  const [body, setBody] = useState(String(payload.body ?? ""))
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (body.trim()) onSubmit({ ...payload, body: body.trim() }) }}>
      <Label htmlFor="reply-body">{t("approvals.forms.replyText")}</Label>
      <Textarea dictation={{ context: "inbox_reply" }} id="reply-body" rows={6} maxLength={4000} value={body} onChange={(e) => setBody(e.target.value)} />
      <p className="text-xs text-muted-foreground">{t("approvals.edit.note")}</p>
      <div className="flex gap-2">
        <Button type="submit" disabled={pending || !body.trim()}>{t("approvals.edit.save")}</Button>
        <Button type="button" variant="outline" onClick={onCancel}>{t("common.cancel")}</Button>
      </div>
    </form>
  )
}
