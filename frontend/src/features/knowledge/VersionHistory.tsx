import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { LocalTime } from "@/components/common/LocalTime"
import type { Article } from "./hooks"

export function VersionHistory({ article, citedVersion }: { article: Article; citedVersion?: number }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const versions = [...article.versions].sort((a, b) => a.version_no - b.version_no)
  const [left, setLeft] = useState(citedVersion ?? versions.at(-2)?.version_no ?? versions[0]?.version_no)
  const [right, setRight] = useState(article.current_version)
  return <section className="space-y-3" aria-labelledby="history-h">
    <h2 id="history-h" className="font-semibold">{t("knowledge.history")}</h2>
    {citedVersion ? <p>{t("knowledge.citedVersion", { version: citedVersion })}</p> : null}
    <div className="grid gap-4 md:grid-cols-2">
      {([left, right] as const).map((selected, index) => {
        const revision = versions.find((v) => v.version_no === selected)
        return <div key={index} className="min-w-0 space-y-2 rounded-md border p-3">
          <label className="block space-y-1">{t(index === 0 ? "knowledge.earlierVersion" : "knowledge.laterVersion")}
            <select className="block min-h-11 w-full rounded-md border bg-background p-2" value={selected} onChange={(e) => (index === 0 ? setLeft : setRight)(Number(e.target.value))}>
              {versions.map((v) => <option key={v.version_no} value={v.version_no}>{t("knowledge.versionLabel", { version: v.version_no })}</option>)}
            </select>
          </label>
          {revision ? <>
            <h3 className="font-medium">{revision.title}</h3>
            <LocalTime iso={revision.created_at} timeZone={businessTimeZone} />
            <p className="text-sm">{revision.tags.join(", ")}</p>
            <pre className="whitespace-pre-wrap break-words text-sm [overflow-wrap:anywhere]">{revision.body_markdown}</pre>
          </> : <p>{t("knowledge.versionMissing")}</p>}
        </div>
      })}
    </div>
  </section>
}
