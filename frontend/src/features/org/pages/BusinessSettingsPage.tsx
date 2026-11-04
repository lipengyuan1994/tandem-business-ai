import { QueryError } from "@/components/common/QueryError"
import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { z } from "zod"
import { useSession } from "@/app/session-context"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { toastError } from "@/lib/errors"
import { useBusiness, useUpdateBusiness } from "../hooks"

const schema = z.object({
  name: z.string().trim().min(1).max(200),
  legal_name: z.string().max(200),
  timezone: z.string().min(1),
  locale: z.string().min(2).max(16),
})
type Values = z.infer<typeof schema>

export function BusinessSettingsPage() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const business = useBusiness()
  const update = useUpdateBusiness()
  const [conflict, setConflict] = useState(false)
  const form = useForm<Values>({ resolver: zodResolver(schema) })
  const canEdit = permissions.has("org.business.write")

  useEffect(() => {
    if (business.data) {
      const b = business.data
      form.reset({ name: b.name, legal_name: b.legal_name ?? "", timezone: b.timezone, locale: b.locale })
    }
  }, [business.data, form])

  if (business.error) return <QueryError error={business.error} onRetry={() => business.refetch()} />
  if (business.isPending) return <Skeleton className="h-64 w-full max-w-xl" />
  if (!business.data) return null
  const zones = Intl.supportedValuesOf("timeZone")

  return (
    <form
      className="max-w-xl space-y-4"
      onSubmit={form.handleSubmit((v) =>
        update.mutate(
          { version: business.data.version, name: v.name, legal_name: v.legal_name || null, timezone: v.timezone, locale: v.locale },
          { onSuccess: () => toast.success(t("common.saved")), onError: (e) => toastError(e, () => setConflict(true)) },
        ),
      )}
    >
      <div className="space-y-1">
        <Label htmlFor="b-name">{t("settings.business.name")}</Label>
        <Input id="b-name" disabled={!canEdit} {...form.register("name")} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="b-legal">{t("settings.business.legalName")}</Label>
        <Input id="b-legal" disabled={!canEdit} {...form.register("legal_name")} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="b-tz">{t("settings.business.timezone")}</Label>
        <select id="b-tz" disabled={!canEdit} className="h-9 w-full rounded-md border bg-background px-3 text-sm" {...form.register("timezone")}>
          {zones.map((z) => <option key={z} value={z}>{z}</option>)}
        </select>
        <p className="text-xs text-muted-foreground">{t("settings.business.timezoneHelp")}</p>
      </div>
      <div className="space-y-1">
        <Label htmlFor="b-locale">{t("settings.business.locale")}</Label>
        <Input id="b-locale" disabled={!canEdit} {...form.register("locale")} />
      </div>
      <p className="text-sm text-muted-foreground">{t("settings.business.currency", { currency: business.data.currency, slug: business.data.slug })}</p>
      {canEdit ? <Button type="submit" disabled={update.isPending}>{t("common.save")}</Button> : null}
      <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { setConflict(false); void business.refetch() }} />
    </form>
  )
}
