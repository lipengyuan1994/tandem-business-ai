import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { CustomerDetail } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toastError } from "@/lib/errors"
import { useAddContactPoint, useRemoveContactPoint, useSetTags } from "../hooks"

type Kind = "email" | "phone"
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

function AddContactForm({ customerId }: { customerId: string }) {
  const { t } = useTranslation()
  const add = useAddContactPoint(customerId)
  const [type, setType] = useState<Kind>("email")
  const [value, setValue] = useState("")
  const [primary, setPrimary] = useState(false)
  const v = value.trim()
  const invalid = v.length > 0 && (v.length < 3 || (type === "email" && !EMAIL.test(v)))
  return (
    <form className="flex flex-wrap items-end gap-2" aria-label={t("crm.contacts.add")} onSubmit={(e) => {
      e.preventDefault()
      if (!v || invalid) return
      add.mutate({ type, value: v, is_primary: primary }, { onSuccess: () => { setValue(""); setPrimary(false); toast.success(t("crm.contacts.added")) }, onError: (err) => toastError(err) })
    }}>
      <div className="space-y-1">
        <Label htmlFor="cp-type">{t("crm.contacts.type")}</Label>
        <select id="cp-type" className="h-9 rounded-md border bg-background px-3 text-sm" value={type} onChange={(e) => setType(e.target.value as Kind)}>
          <option value="email">{t("crm.fields.email")}</option>
          <option value="phone">{t("crm.fields.phone")}</option>
        </select>
      </div>
      <div className="min-w-40 flex-1 space-y-1">
        <Label htmlFor="cp-value">{t("crm.contacts.value")}</Label>
        <Input id="cp-value" type={type === "email" ? "email" : "tel"} maxLength={320} value={value} aria-invalid={invalid} onChange={(e) => setValue(e.target.value)} />
      </div>
      <label className="flex h-9 items-center gap-2 text-sm"><input type="checkbox" className="size-4" checked={primary} onChange={(e) => setPrimary(e.target.checked)} />{t("crm.contacts.makePrimary")}</label>
      <Button type="submit" disabled={!v || invalid || add.isPending}>{t("common.add")}</Button>
      {invalid ? <p role="alert" className="w-full text-sm text-destructive">{t("crm.contacts.invalid")}</p> : null}
    </form>
  )
}

function TagsEditor({ customer, canEdit }: { customer: CustomerDetail; canEdit: boolean }) {
  const { t } = useTranslation()
  const save = useSetTags(customer.id)
  const [tag, setTag] = useState("")
  const put = (tags: string[], done?: () => void) => save.mutate(tags, { onSuccess: done, onError: (e) => toastError(e) })
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium">{t("crm.fields.tags")}</h3>
      {customer.tags.length === 0 ? <p className="text-sm text-muted-foreground">{t("crm.tags.none")}</p> : (
        <ul className="flex flex-wrap gap-1" aria-label={t("crm.fields.tags")}>
          {customer.tags.map((x) => (
            <li key={x}><Badge variant="outline" className="gap-1">{x}{canEdit ? <button type="button" className="ml-1 rounded-sm px-1 hover:bg-accent" disabled={save.isPending}
              aria-label={t("crm.tags.removeFor", { tag: x })} onClick={() => put(customer.tags.filter((y) => y !== x))}>×</button> : null}</Badge></li>
          ))}
        </ul>
      )}
      {canEdit ? (
        <form className="flex gap-2" onSubmit={(e) => {
          e.preventDefault()
          const next = tag.trim().toLowerCase()
          if (!next || customer.tags.includes(next)) return setTag("")
          put([...customer.tags, next], () => setTag(""))
        }}>
          <Input aria-label={t("crm.tags.add")} placeholder={t("crm.tags.placeholder")} maxLength={50} className="max-w-56" value={tag} onChange={(e) => setTag(e.target.value)} />
          <Button type="submit" variant="outline" disabled={!tag.trim() || save.isPending}>{t("crm.tags.addButton")}</Button>
        </form>
      ) : null}
    </div>
  )
}

/** Emails, phone numbers and tags. Each email or phone number can belong to only one customer. */
export function ContactsCard({ customer, canEdit }: { customer: CustomerDetail; canEdit: boolean }) {
  const { t } = useTranslation()
  const remove = useRemoveContactPoint()
  return (
    <Card>
      <CardHeader><CardTitle className="text-base">{t("crm.contacts.title")}</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        {customer.contact_points.length === 0 ? <p className="text-sm text-muted-foreground">{t("crm.contacts.none")}</p> : (
          <ul className="divide-y rounded-md border" aria-label={t("crm.contacts.title")}>
            {customer.contact_points.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2 p-2 text-sm">
                <span className="w-14 text-muted-foreground">{t(`crm.fields.${p.type}`)}</span>
                <span className="break-all">{p.value}</span>
                {p.is_primary ? <Badge variant="secondary">{t("crm.contacts.primary")}</Badge> : null}
                {canEdit ? <Button size="sm" variant="ghost" className="ml-auto" disabled={remove.isPending} aria-label={t("crm.contacts.removeFor", { value: p.value })}
                  onClick={() => remove.mutate(p.id, { onSuccess: () => toast.success(t("crm.contacts.removed")), onError: (e) => toastError(e) })}>{t("crm.contacts.remove")}</Button> : null}
              </li>
            ))}
          </ul>
        )}
        {canEdit ? <AddContactForm customerId={customer.id} /> : null}
        <TagsEditor customer={customer} canEdit={canEdit} />
      </CardContent>
    </Card>
  )
}
