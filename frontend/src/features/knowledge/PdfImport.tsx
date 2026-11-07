import { Link } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { describeError } from "@/lib/errors"
import { usePdfImport } from "./hooks"

export function PdfImport() {
  const { t } = useTranslation()
  const mutation = usePdfImport()
  const [file, setFile] = useState<File>()
  const [error, setError] = useState<string>()
  return <section className="space-y-2 rounded-md border p-3" aria-label={t("knowledge.pdf.title")}>
    <h2 className="font-medium">{t("knowledge.pdf.title")}</h2>
    <p className="text-sm text-muted-foreground" id="pdf-limits">{t("knowledge.pdf.limits")}</p>
    <Input type="file" accept="application/pdf,.pdf" aria-label={t("knowledge.pdf.file")} aria-describedby="pdf-limits" disabled={mutation.isPending} onChange={(event) => {
      const chosen = event.target.files?.[0]
      mutation.reset()
      setError(undefined)
      setFile(undefined)
      if (chosen && (chosen.type !== "application/pdf" || chosen.size > 10 * 1024 * 1024)) setError(t("knowledge.pdf.invalid"))
      else setFile(chosen)
    }} />
    <Button disabled={!file || mutation.isPending} onClick={() => {
      if (file) mutation.mutate(file)
    }}>{mutation.isPending ? t("knowledge.pdf.progress") : t("knowledge.pdf.import")}</Button>
    {mutation.isPending && <p role="status">{t("knowledge.pdf.progress")}</p>}
    {(error || mutation.error) && <p role="alert">{error ?? describeError(mutation.error)}</p>}
    {mutation.data && <div className="space-y-2"><p role="status">{t("knowledge.pdf.review")}</p><ul>{mutation.data.drafts.map((draft) => <li key={draft.id}><Link className="record-link" to="/knowledge/$articleId" params={{ articleId: draft.id }}>{draft.title}</Link><span className="ml-2 text-sm text-muted-foreground">{t("knowledge.pdf.pages", { start: draft.source_page_start, end: draft.source_page_end })}</span></li>)}</ul></div>}
  </section>
}
