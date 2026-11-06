export function normalizeEmbedOrigins(text: string, development: boolean): string[] {
  const rows = text.split(/\n/).map((v) => v.trim()).filter(Boolean)
  if (rows.length > 10) throw new Error("origins")
  return [...new Set(rows.map((row) => {
    const url = new URL(row)
    if (/[\s*<>\\'"]/.test(row) || !/^[a-z]+:\/\/[^/?#]+$/i.test(row) || url.username || url.password || !url.hostname || url.port === "0" || (url.protocol !== "https:" && !(development && url.protocol === "http:" && url.hostname === "localhost"))) throw new Error("origin")
    return url.origin
  }))]
}

