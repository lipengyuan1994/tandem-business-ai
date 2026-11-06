import type { Agent, TeamItem } from "./team-api"

export const TEAM_NOW = "2026-11-06T17:00:00Z"
export const TEAM_ACTION = "inventory.create_reorder_draft"
export const TEAM_AGENT: Agent = {
  key: "inventory", name: "Inventory", job: "Watches stock and drafts reorders.", version: 3, enabled: true, status: "on", reason: null,
  daily_cost_limit_usd: "1.00", cost_today_micros: 120000, last_activity_at: TEAM_NOW,
  today: { runs: 4, proposals_created: 3, actions_executed_by_people: 1, actions_executed_by_rules: 2, handed_to_person: 1 },
  waiting: { count: 2, oldest_at: TEAM_NOW },
  autonomy: [{ action_type: TEAM_ACTION, active: true, daily_cap: 10, level: "act_notify", shadow: true, version: 1 }],
}
export function teamItem(id: string, extra: Partial<TeamItem> = {}): TeamItem {
  return { id, kind: "run", agent: "inventory", created_at: TEAM_NOW, title: `Stock check ${id}`, status: "succeeded", cost_micros: 20000, delivery_state: "none", reasons: [], correlation_id: "chain-1", ...extra }
}
