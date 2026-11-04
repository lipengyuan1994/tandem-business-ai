> **Superseded on 2026-11-03 (UTC).** Historical pre-event draft kept for provenance. The current plan is [01-product-and-scope.md](roadmap/01-product-and-scope.md). Do not implement from this file.

# Product scope and preparation boundary

A customer-owned operations workspace for service and retail SMBs. Owners and staff should find customer context, conversations, calendars, inventory, campaign drafts, approvals, and an evidence trail in one place. General SMB scope; no dental or medical specialization.

| Module | Prepared now | Planned after kickoff |
|---|---|---|
| CRM | Fictional customer table | Permissioned contacts and interaction context |
| Conversations | Scripted text fixture | Grounded responses with source references |
| Browser voice | Disabled control and architecture | Consented browser audio to deployment-owned STT/TTS |
| Scheduling | Example requested appointment | Availability checks; staff-approved booking with conflict handling |
| Inventory | Fictional stock snapshots | Stock review, proposed reorder, approved changes |
| Marketing | Authored sample draft | Draft generation; consent-aware staff-approved outreach |
| Approvals | Static pending example | Identity-bound review of exact proposed changes |
| Audit history | Explicitly synthetic rows | Recorded proposal, decision, execution receipt, failure and actor |

Proposed event slice: a service request becomes a proposed appointment and CRM update, staff review the exact changes, then a bounded executor writes to a synthetic business sandbox and returns evidence. This entire workflow is documentation only before kickoff. Inventory and marketing stay secondary if time is tight. Voice is optional, gated on privacy and performance.

Success measures to establish during the event: task completion, staff corrections, unsupported claims, rejected unauthorized actions, duplicate effects, latency and compute cost. No measured values or promised savings yet.

Non-goals for this preparation: working AI, agent SDK wiring, prompts that drive an agent, connectors, authentication implementation, send/book/purchase operations, production deployment, multi-customer hosting, telephone integration or real business data. Schema constraints are sample data contracts, not an implemented authorization system.
