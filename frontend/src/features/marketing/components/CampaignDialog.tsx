import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { Clause } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { toastError } from "@/lib/errors"
import { useCreateCampaign } from "../hooks"
import { SegmentBuilder } from "./SegmentBuilder"

export function CampaignDialog({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const create = useCreateCampaign()
  const [name, setName] = useState("")
  const [subject, setSubject] = useState("")
  const [body, setBody] = useState("")
  const [clauses, setClauses] = useState<Clause[]>([])
  const ready = name.trim() && subject.trim() && body.trim() && clauses.length > 0

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("marketing.new")}</DialogTitle>
          <DialogDescription>{t("marketing.newHint")}</DialogDescription>
        </DialogHeader>
        <form id="campaign-form" className="space-y-4" onSubmit={(e) => {
          e.preventDefault()
          if (!ready) return
          create.mutate({ name: name.trim(), purpose: null, clauses, subject: subject.trim(), body_markdown: body },
            { onSuccess: (c) => { onOpenChange(false); void navigate({ to: "/marketing/$campaignId", params: { campaignId: c.id } }) }, onError: (err) => toastError(err) })
        }}>
          <div className="space-y-1">
            <Label htmlFor="mc-name">{t("marketing.fields.name")}</Label>
            <Input id="mc-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">{t("marketing.fields.audience")}</legend>
            <SegmentBuilder clauses={clauses} onChange={setClauses} />
          </fieldset>
          <div className="space-y-1">
            <Label htmlFor="mc-subject">{t("marketing.fields.subject")}</Label>
            <Input id="mc-subject" value={subject} maxLength={200} onChange={(e) => setSubject(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="mc-body">{t("marketing.fields.body")}</Label>
            <Textarea id="mc-body" rows={6} value={body} onChange={(e) => setBody(e.target.value)} />
          </div>
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button type="submit" form="campaign-form" disabled={!ready || create.isPending}>{t("marketing.createDraft")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
