import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ApiProblem } from "@/lib/problem"
import { toastError } from "@/lib/errors"
import { type Article, type ArticleInput, type Suggestion, useArticleStatus, useSaveArticle } from "./hooks"
import { MarkdownPreview } from "./MarkdownPreview"
import { VersionHistory } from "./VersionHistory"

function slugFor(title: string) { return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 100).replace(/-$/, "") }
export function ArticleEditor({ article, suggestion, citedVersion, onSaved, onReload }: { article?: Article; suggestion?: Suggestion; citedVersion?: number; onSaved: (a: Article) => void; onReload: () => void }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const latest = article?.versions.find((v) => v.version_no === article.current_version)
  const [title, setTitle] = useState(article?.title ?? suggestion?.question_text.slice(0, 200) ?? "")
  const [slug, setSlug] = useState(article?.slug ?? slugFor(title))
  const [body, setBody] = useState(latest?.body_markdown ?? "")
  const [tags, setTags] = useState(latest?.tags.join(", ") ?? "")
  const [visibility, setVisibility] = useState<ArticleInput["visibility"]>(article?.visibility === "internal" ? "internal" : "public")
  const [confirm, setConfirm] = useState<"publish" | "archive" | null>(null)
  const [conflict, setConflict] = useState(false)
  const save = useSaveArticle(article, suggestion)
  const status = useArticleStatus(article?.id ?? "")
  const write = permissions.has("org.settings.write")
  const dirty = article && (title !== article.title || body !== latest?.body_markdown || tags !== latest?.tags.join(", ") || visibility !== article.visibility)
  const pending = save.isPending || status.isPending
  const tagList = tags.split(",").map((s) => s.trim()).filter(Boolean)
  const validTags = tagList.length <= 20 && tagList.every((tag) => tag.length <= 50)
  const onError = (error: unknown) => { if (error instanceof ApiProblem && error.isConflict) setConflict(true); else toastError(error) }
  return <div className="space-y-6">
    {article ? <p className="text-sm">{t(`knowledge.statuses.${article.status}`)} · {t(`knowledge.visibilities.${article.visibility}`)} · {t("knowledge.versionLabel", { version: article.current_version })}{article.published_version ? ` · ${t("knowledge.publishedVersion", { version: article.published_version })}` : ""}</p> : null}
    <form className="space-y-4" onSubmit={(e) => {
      e.preventDefault()
      if (!write || conflict || !validTags) return
      save.mutate({ title: title.trim(), slug, body_markdown: body, tags: tagList, visibility }, { onSuccess: onSaved, onError })
    }}>
      <fieldset className="space-y-4" disabled={!write || pending || conflict}>
        <div className="space-y-1"><Label htmlFor="kb-title">{t("knowledge.title")}</Label><Input id="kb-title" required maxLength={200} value={title} onChange={(e) => { const value = e.target.value; if (!article && slug === slugFor(title)) setSlug(slugFor(value)); setTitle(value) }} /></div>
        {!article ? <div className="space-y-1"><Label htmlFor="kb-slug">{t("knowledge.slug")}</Label><Input id="kb-slug" required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxLength={100} value={slug} onChange={(e) => setSlug(e.target.value)} /></div> : null}
        <div className="space-y-1"><Label htmlFor="kb-body">{t("knowledge.body")}</Label><Textarea id="kb-body" required maxLength={20000} rows={12} value={body} onChange={(e) => setBody(e.target.value)} /></div>
        <div className="space-y-1"><Label htmlFor="kb-tags">{t("knowledge.tags")}</Label><Input id="kb-tags" value={tags} aria-invalid={!validTags} onChange={(e) => setTags(e.target.value)} />{!validTags ? <p role="alert">{t("knowledge.tagsInvalid")}</p> : null}</div>
        <label className="block space-y-1">{t("knowledge.visibility")}<select className="block min-h-11 rounded-md border bg-background p-2" value={visibility} onChange={(e) => setVisibility(e.target.value as ArticleInput["visibility"])}>{["public", "internal"].map((v) => <option key={v} value={v}>{t(`knowledge.visibilities.${v}`)}</option>)}</select></label>
      </fieldset>
      <PermissionGate permission="org.settings.write"><Button type="submit" disabled={pending || conflict || !validTags || !title.trim() || !body.trim()}>{t(article ? "knowledge.saveVersion" : "knowledge.saveDraft")}</Button></PermissionGate>
    </form>
    <section aria-label={t("knowledge.preview")} className="space-y-2 rounded-md border p-4"><h2 className="font-semibold">{t("knowledge.preview")}</h2><p className="text-sm text-muted-foreground">{t("knowledge.previewHelp")}</p><MarkdownPreview body={body} /></section>
    {article ? <PermissionGate permission="org.settings.write"><div className="flex flex-wrap gap-2">
      <Button disabled={pending || conflict || !!dirty || article.status === "archived"} onClick={() => setConfirm("publish")}>{t("knowledge.publish")}</Button>
      <Button variant="outline" disabled={pending || conflict || !!dirty || article.status === "archived"} onClick={() => setConfirm("archive")}>{t("knowledge.archive")}</Button>
      {dirty ? <p className="text-sm">{t("knowledge.saveFirst")}</p> : null}
    </div></PermissionGate> : null}
    {article ? <VersionHistory article={article} citedVersion={citedVersion} /> : null}
    <ConfirmDialog open={!!confirm} onOpenChange={(open) => { if (!open) setConfirm(null) }} title={t(confirm === "archive" ? "knowledge.archiveTitle" : "knowledge.publishTitle")} description={t(confirm === "archive" ? "knowledge.archiveHelp" : "knowledge.publishHelp")} confirmLabel={t(confirm === "archive" ? "knowledge.archiveTitle" : "knowledge.publishTitle")} destructive={confirm === "archive"} pending={pending} onConfirm={() => { if (confirm && article) status.mutate({ action: confirm, version: article.version }, { onSuccess: (saved) => { setConfirm(null); onSaved(saved) }, onError: (e) => { setConfirm(null); onError(e) } }) }} />
    <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={onReload} />
  </div>
}
