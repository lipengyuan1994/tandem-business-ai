// Which values of an `ExtractOut` the server could not find in the supplier's text (`unverified_fields`).
// The contract does not fix the spelling of a line field, so these accept `lines.0.unit_price`, `lines[0].unit_price`
// and `lines.<item_id>.unit_price`; top-level fields are plain names such as `lead_time_days`.
import type { TFunction } from "i18next"

function parts(field: string): string[] {
  return field.replace(/\[(\d+)\]/g, ".$1").split(".").filter(Boolean)
}

export function unverifiedTop(fields: string[], name: string): boolean {
  return fields.some((f) => f === name)
}

export function unverifiedLine(fields: string[], index: number, itemId: string, name: string): boolean {
  return fields.some((f) => {
    const p = parts(f)
    return p[0] === "lines" && (p[1] === String(index) || p[1] === itemId) && p[2] === name
  })
}

/** "Brake pads: price" or "lead time"; unknown spellings are shown as written, with underscores removed. */
export function unverifiedLabel(t: TFunction, field: string, found: { item_id: string }[], lines: { item_id: string; name: string }[]): string {
  const p = parts(field)
  const known = (name: string | undefined) => (name ? t(`ai.paste.fields.${name}`, { defaultValue: name.replaceAll("_", " ") }) : "")
  if (p[0] === "lines" && p.length >= 3) {
    const itemId = /^\d+$/.test(p[1] ?? "") ? found[Number(p[1])]?.item_id : p[1]
    return `${lines.find((l) => l.item_id === itemId)?.name ?? itemId ?? p[1]}: ${known(p[2])}`
  }
  return known(p.length === 1 ? p[0] : p.join(" "))
}
