import { QueryError } from "@/components/common/QueryError"
import { Link } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { EmptyState } from "@/components/common/EmptyState"
import { LocalTime } from "@/components/common/LocalTime"
import { PageHeader } from "@/components/common/PageHeader"
import { PermissionGate } from "@/components/common/PermissionGate"
import { StatusBadge } from "@/components/common/StatusBadge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { toastError } from "@/lib/errors"
import { CampaignDialog } from "./components/CampaignDialog"
import { useAddSuppression, useCampaigns, useSuppressions } from "./hooks"

export function MarketingPage() {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const campaigns = useCampaigns()
  const [creating, setCreating] = useState(false)
  return (
    <>
      <PageHeader title={t("nav.marketing")} description={t("marketing.description")}
        actions={<PermissionGate permission="marketing.campaign.write"><Button onClick={() => setCreating(true)}>{t("marketing.new")}</Button></PermissionGate>} />
      <Tabs defaultValue="campaigns">
        <TabsList>
          <TabsTrigger value="campaigns">{t("marketing.tabs.campaigns")}</TabsTrigger>
          <TabsTrigger value="suppressions">{t("marketing.tabs.suppressions")}</TabsTrigger>
        </TabsList>
        <TabsContent value="campaigns" className="pt-4">
          {campaigns.error ? <QueryError error={campaigns.error} onRetry={() => campaigns.refetch()} /> : campaigns.isPending ? <Skeleton className="h-40 w-full" /> : !campaigns.data?.length ? <EmptyState title={t("marketing.empty")} description={t("marketing.emptyHint")} /> : (
            <ul className="divide-y rounded-lg border">
              {campaigns.data.map((c) => (
                <li key={c.id}>
                  <Link to="/marketing/$campaignId" params={{ campaignId: c.id }} className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-accent">
                    <span>
                      <span className="block font-medium">{c.name}</span>
                      <span className="block text-sm text-muted-foreground">{c.current_revision?.subject} · <LocalTime iso={c.created_at} timeZone={businessTimeZone} pattern="d MMM yyyy" /></span>
                    </span>
                    <StatusBadge status={c.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </TabsContent>
        <TabsContent value="suppressions" className="pt-4"><Suppressions /></TabsContent>
      </Tabs>
      {creating ? <CampaignDialog onOpenChange={setCreating} /> : null}
    </>
  )
}

function Suppressions() {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const list = useSuppressions()
  const add = useAddSuppression()
  const [value, setValue] = useState("")
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">{t("marketing.suppressionsHint")}</p>
      <PermissionGate permission="marketing.campaign.write">
        <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (value.trim()) add.mutate({ value: value.trim(), reason: "manual" }, { onSuccess: () => setValue(""), onError: (err) => toastError(err) }) }}>
          <div className="space-y-1">
            <Label htmlFor="sup-value">{t("marketing.suppressionAdd")}</Label>
            <Input id="sup-value" type="email" value={value} onChange={(e) => setValue(e.target.value)} />
          </div>
          <Button type="submit" disabled={add.isPending}>{t("common.add")}</Button>
        </form>
      </PermissionGate>
      {list.error ? <QueryError error={list.error} onRetry={() => list.refetch()} /> : list.isPending ? <Skeleton className="h-24 w-full" /> : !list.data?.length ? <p className="text-sm text-muted-foreground">{t("marketing.noSuppressions")}</p> : (
        <ul className="divide-y rounded-lg border text-sm">
          {list.data.map((s) => (
            <li key={s.id} className="flex items-center justify-between px-4 py-2">
              <span>{s.value_normalized}</span>
              <span className="text-muted-foreground">{t(`marketing.reasons.${s.reason}`)} · <LocalTime iso={s.created_at} timeZone={businessTimeZone} pattern="d MMM yyyy" /></span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
