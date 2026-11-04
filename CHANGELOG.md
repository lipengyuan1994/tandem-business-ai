# Changelog

Release notes for operators. Each release lists database migrations and any manual step (with "before" or "after" the upgrade). The upgrade runbook is `docs/runbooks/upgrade.md`.

## 0.1.0 (not yet released)

First version: platform, core services, business modules (CRM, catalog, inventory, scheduling, conversations, dashboard) without AI, packaging and deployment files. No upgrade path from earlier versions (there are none).

Also in 0.1.0: marketing campaigns, System page, owner data export and customer erasure (migrations `20261027_0900_admin_ops_core` and `20261027_1500_erasure_hardening`; no manual step).

Also in 0.1.0: sales (quotes, orders, invoices, credit notes, accounting CSV; migrations `20261028_0900_sales_core`, `20261028_0910_sales_tax_rate`; set the default sales tax rate under Settings → Policies after upgrading).

Also in 0.1.0: purchasing (suppliers, purchase orders, goods receipts; migration `20261028_1000_purchasing_core`; no manual step).

Also in 0.1.0: e-mail over SMTP (set `SMTP_URL` to send real e-mail; without it messages stay simulated; configure Keycloak mail by hand, see `docs/runbooks/email.md`).

Also in 0.1.0: regulated data profile (`DATA_PROFILE`, default `standard`; migrations `20261028_1100_regulated_profile`, `20261028_1200_regulated_hardening`; see `docs/runbooks/regulated-profile.md`) and per-business terminology under Settings.

Also in 0.1.0: payment reminders for overdue invoices; procurement quote requests with a public supplier quote page, explained comparison, purchase orders from quotes and late-order follow-ups (migrations `20261031_1000_platform_prerequisites`, `20261031_1100_invoice_reminders`, `20261031_1200_purchasing_quotes`; route `/q/*` served by the public bundle — check custom proxies).

Also in 0.1.0: supplier quote e-mails and payment reminders stay simulated without `SMTP_URL`, like other notifications (they failed before); approval previews show item names and amounts in normal units; staff can copy a pending supplier's quote link. No migration, no manual step. Seeding demo data never sends real e-mail, even with `SMTP_URL` set.

Also in 0.1.0 (security hardening, 2026-11-03): the web (Caddy), PostgreSQL and Keycloak containers run without root and with all capabilities dropped (Caddy keeps only the right to bind ports 80/443); Caddy sends no `Server` header and masks capability tokens and login codes in its error log; Keycloak's master realm is no longer reachable through Caddy; the API description (`/api/v1/openapi.json`, `/api/v1/docs`) is off in production; 429 answers carry `Retry-After`; new command `demo-logins` creates logins for the synthetic demo staff only (see `docs/runbooks/install.md`). **Deployment files changed** (`compose.yml`, `upgrade.sh`, `smoke.sh`): follow the "deployment files change" procedure in `docs/runbooks/upgrade.md`; `upgrade.sh` hands the Caddy volumes to the new non-root user before starting it (a manual upgrade must run the chown step in that runbook first, or certificates cannot be read).

Also in 0.1.0 (AI layer, built after the event kickoff on 2026-11-03): module `ai` (migration `20261103_1600_ai_core`): model gateway (OpenAI-compatible incl. Vultr Serverless Inference, Anthropic), router with per-business policies and a daily token budget, agent runner with run/usage recording, 26 tools (read, slot hold, propose-for-approval), and use cases: inbound-message intake, staff copilot chat (server-sent events), conversation summary with a suggested reply, reorder notes, daily briefing, procurement agent (quote requests, supplier reply extraction, comparison, PO proposal, follow-ups). Every change the AI suggests is a proposal a person approves. **Manual steps:** create `config/ai-routes.yaml` from `deploy/config/ai-routes.example.yaml` (install and upgrade never overwrite it) and set `VULTR_INFERENCE_API_KEY` in `.env`; without both, AI stays off ("not configured"). Deployment files changed (`compose.yml`, `Caddyfile`, `install.sh`, `upgrade.sh`, `smoke.sh`, `env.prod.template`): follow the "deployment files change" procedure in `docs/runbooks/upgrade.md`.
