import { QueryError } from "@/components/common/QueryError"
import { Link, useParams } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { toastError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"
import { useAddRevision, useAudiencePreview, useCampaign, useCancelCampaign, useSubmitCampaign } from "./hooks"

const REASONS = ["no_consent", "suppressed", "no_contact", "archived"] as const

export function CampaignPage() {
  const { t } = useTranslation()
  const { campaignId } = useParams({ from: "/app/marketing/$campaignId" })
  const { businessTimeZone } = useSession()
  const campaign = useCampaign(campaignId)
  const [offset, setOffset] = useState(0)
  const preview = useAudiencePreview(campaign.data?.segment_id, offset)
  const addRevision = useAddRevision(campaignId)
  const submit = useSubmitCampaign(campaignId)
  const cancel = useCancelCampaign(campaignId)
  const [draft, setDraft] = useState<{ subject: string; body: string } | null>(null)
  const [confirmEdit, setConfirmEdit] = useState(false)
  const [blocked, setBlocked] = useState<string | null>(null)
  const c = campaign.data

  if (campaign.error) return <QueryError error={campaign.error} onRetry={() => campaign.refetch()} notFound={t("marketing.notFound")} />
  if (campaign.isPending) return <Skeleton className="h-96 w-full" />
  if (!c) return <EmptyState title={t("marketing.notFound")} />
  const current = c.current_revision
  const editable = c.status === "draft" || c.status === "in_review"
  const subject = draft?.subject ?? current?.subject ?? ""
  const body = draft?.body ?? current?.body_markdown ?? ""
  const changed = draft !== null && (draft.subject !== current?.subject || draft.body !== current?.body_markdown)
  const saveRevision = () => addRevision.mutate({ subject, body_markdown: body }, { onSuccess: () => { setDraft(null); setConfirmEdit(false) }, onError: (e) => toastError(e) })

  return (
    <>
      <div className="mb-4"><Link className="record-link" to="/marketing">{t("marketing.back")}</Link></div>
      <PageHeader title={c.name} description={t("marketing.channelEmail")} actions={<StatusBadge status={c.status} />} />
      {c.status === "in_review" ? (
        <Alert className="mb-4">
          <AlertTitle>{t("marketing.inReview.title")}</AlertTitle>
          <AlertDescription>
            {t("marketing.inReview.text")}{" "}
            {c.proposal_id ? <Link className="underline" to="/approvals/$proposalId" params={{ proposalId: c.proposal_id }}>{t("marketing.inReview.open")}</Link> : null}
          </AlertDescription>
        </Alert>
      ) : null}
      {c.status === "sent" ? <Alert className="mb-4"><AlertTitle>{t("marketing.sent.title")}</AlertTitle><AlertDescription>{t("marketing.sent.text")}</AlertDescription></Alert> : null}
      {blocked ? <Alert variant="destructive" className="mb-4" role="alert"><AlertDescription>{blocked}</AlertDescription></Alert> : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem] [&>*]:min-w-0">
        <section aria-labelledby="content-h" className="space-y-4">
          <h2 id="content-h" className="font-medium">{t("marketing.message")}{current ? ` · ${t("marketing.revisionNo", { n: current.revision_no })}` : ""}</h2>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (c.status === "in_review") setConfirmEdit(true); else saveRevision() }}>
            <div className="space-y-1">
              <Label htmlFor="cp-subject">{t("marketing.fields.subject")}</Label>
              <Input id="cp-subject" value={subject} disabled={!editable} maxLength={200} onChange={(e) => setDraft({ subject: e.target.value, body })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="cp-body">{t("marketing.fields.body")}</Label>
              <Textarea id="cp-body" rows={8} value={body} disabled={!editable} onChange={(e) => setDraft({ subject, body: e.target.value })} />
            </div>
            <PermissionGate permission="marketing.campaign.write">
              {editable ? <p className="text-sm text-muted-foreground">{changed ? t("marketing.saveFirst") : t("marketing.editHint")}</p> : null}
              {editable ? <Button type="submit" variant="outline" disabled={!changed || addRevision.isPending}>{t("marketing.saveRevision")}</Button> : null}
            </PermissionGate>
          </form>
          <Card>
            <CardHeader><CardTitle className="text-sm">{t("marketing.previewOfMessage")}</CardTitle></CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p className="font-medium">{subject}</p>
              {body.split(/\n{2,}/).map((para, i) => <p key={`${i}-${para.slice(0, 12)}`} className="whitespace-pre-wrap break-words">{para}</p>)}
              <p className="border-t pt-2 text-xs text-muted-foreground">{t("marketing.footerNote")}</p>
            </CardContent>
          </Card>
          <section aria-labelledby="rev-h">
            <h3 id="rev-h" className="mb-1 text-sm font-medium">{t("marketing.revisions")}</h3>
            <ol className="space-y-1 text-sm">
              {c.revisions.map((r) => (
                <li key={r.id}>{t("marketing.revisionLine", { n: r.revision_no, by: t(`marketing.by.${r.created_by_kind}`) })} · <LocalTime iso={r.created_at} timeZone={businessTimeZone} pattern="d MMM HH:mm" /> · {r.subject}</li>
              ))}
            </ol>
          </section>
        </section>

        <aside className="space-y-4">
          <Card>
            <CardHeader><CardTitle className="text-sm">{t("marketing.audience")}</CardTitle></CardHeader>
            <CardContent className="space-y-3 text-sm">
              {preview.error ? <QueryError error={preview.error} onRetry={() => preview.refetch()} /> : preview.isPending ? <Skeleton className="h-24 w-full" /> : preview.data ? (
                <>
                  <p><span className="text-2xl font-semibold">{preview.data.eligible_count}</span> {t("marketing.eligible", { count: preview.data.eligible_count })}</p>
                  <ul className="space-y-0.5 text-muted-foreground" aria-label={t("marketing.excludedTitle")}>
                    {REASONS.filter((r) => preview.data!.excluded_counts[r]).map((r) => <li key={r}>{t(`marketing.excluded.${r}`, { count: preview.data!.excluded_counts[r] })}</li>)}
                    {REASONS.every((r) => !preview.data!.excluded_counts[r]) ? <li>{t("marketing.noneExcluded")}</li> : null}
                  </ul>
                  <ul aria-label={t("marketing.recipients")} className="divide-y rounded border">
                    {preview.data.members.map((m) => <li key={m.customer_id} className="px-2 py-1">{m.display_name}</li>)}
                  </ul>
                  <div className="flex gap-2">
                    <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 10))}>{t("marketing.prev")}</Button>
                    <Button size="sm" variant="outline" disabled={offset + 10 >= preview.data.eligible_count} onClick={() => setOffset(offset + 10)}>{t("marketing.next")}</Button>
                  </div>
                  {c.snapshot ? <p className="text-xs text-muted-foreground">{t("marketing.frozen", { n: c.snapshot.eligible_count })}</p> : <p className="text-xs text-muted-foreground">{t("marketing.liveNote")}</p>}
                </>
              ) : null}
            </CardContent>
          </Card>
          <PermissionGate permission="marketing.campaign.write">
            <div className="flex flex-col gap-2">
              {c.status === "draft" ? (
                <Button disabled={submit.isPending || preview.isFetching || !!preview.error || changed || (preview.data?.eligible_count ?? 0) === 0}
                  onClick={() => submit.mutate(undefined, { onError: (e) => (e instanceof ApiProblem && e.code === "marketing.no_eligible_recipients" ? setBlocked(t("marketing.noRecipients")) : toastError(e)) })}>
                  {t("marketing.submit")}
                </Button>
              ) : null}
              {c.status === "draft" ? <p className="text-sm text-muted-foreground">{preview.error || preview.isFetching ? t("marketing.previewRequired") : changed ? t("marketing.saveFirst") : !preview.data?.eligible_count ? t("marketing.noRecipients") : null}</p> : null}
              {editable ? <Button variant="ghost" onClick={() => cancel.mutate(undefined, { onError: (e) => toastError(e) })}>{t("marketing.cancel")}</Button> : null}
            </div>
          </PermissionGate>
        </aside>
      </div>
      <ConfirmDialog open={confirmEdit} onOpenChange={setConfirmEdit} title={t("marketing.editConfirm.title")} description={t("marketing.editConfirm.text")}
        confirmLabel={t("marketing.editConfirm.confirm")} pending={addRevision.isPending} onConfirm={saveRevision} />
    </>
  )
}
