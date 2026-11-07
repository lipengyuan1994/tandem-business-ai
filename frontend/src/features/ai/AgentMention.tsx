import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import type { AddressableAgent } from "./team-api"

export function AgentMention({ agents, selected, onSelect, text, onTextChange, disabled }: {
  agents: AddressableAgent[]; selected?: string; onSelect: (key: string | undefined) => void; text: string; onTextChange: (value: string) => void; disabled: boolean
}) {
  const { t } = useTranslation()
  const [dismissed, setDismissed] = useState<string>()
  const match = /(?:^|\s)@([^\s@]*)$/.exec(text)
  const selectedAgent = agents.find((agent) => agent.key === selected)
  const candidates = agents.filter((agent) => agent.enabled && agent.name.toLowerCase().includes((match?.[1] ?? "").toLowerCase()))
  return <div className="space-y-2">
    {selected ? <div className="flex items-center gap-2 rounded-md border p-2 text-sm"><span>{t("ai.team.to", { name: selectedAgent?.name ?? selected })}</span><Button type="button" size="sm" variant="ghost" disabled={disabled && selectedAgent?.enabled} onClick={() => onSelect(undefined)}>{t("ai.team.removeAgent")}</Button></div> : null}
    {match && !disabled && dismissed !== text ? <div role="group" aria-label={t("ai.team.mentionTitle")} className="space-y-1 rounded-md border p-2">
      {candidates.map((agent) => <Button type="button" key={agent.key} variant="ghost" size="sm" onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setDismissed(text) } }} onClick={() => { onSelect(agent.key); onTextChange(text.slice(0, text.lastIndexOf("@"))); document.getElementById("assistant-input")?.focus() }}>{agent.name}</Button>)}
    </div> : null}
    <p className="text-xs text-muted-foreground">{t("ai.team.mentionHint")}</p>
  </div>
}
