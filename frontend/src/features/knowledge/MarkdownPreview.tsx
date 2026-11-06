import { Fragment } from "react"

// A deliberately small, safe Markdown subset. React escapes all text; raw HTML and images are never rendered.
function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>
    const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part)
    if (link && /^(https?:\/\/|\/[^/]|#)/i.test(link[2]!)) return <a key={i} className="record-link" href={link[2]} rel="noreferrer">{link[1]}</a>
    return <Fragment key={i}>{part}</Fragment>
  })
}
export function MarkdownPreview({ body }: { body: string }) {
  return <div className="space-y-2 break-words [overflow-wrap:anywhere]">{body.split(/\n\s*\n/).map((block, i) => {
    const heading = /^(#{1,3}) (.+)$/.exec(block)
    if (heading) {
      const Tag = heading[1]!.length === 1 ? "h3" : heading[1]!.length === 2 ? "h4" : "h5"
      return <Tag key={i} className="font-semibold">{inline(heading[2]!)}</Tag>
    }
    const lines = block.split("\n")
    if (lines.every((line) => /^[-*] /.test(line))) return <ul key={i} className="list-disc pl-5">{lines.map((line, j) => <li key={j}>{inline(line.slice(2))}</li>)}</ul>
    return <p key={i} className="whitespace-pre-wrap">{inline(block)}</p>
  })}</div>
}
