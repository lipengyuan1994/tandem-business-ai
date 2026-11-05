# 13 · Risks and open questions

## Top risks

| # | Risk | Likelihood | Impact | Mitigation | Owner of the mitigation |
|---|---|---|---|---|---|
| R1 | **Eligibility:** organisers consider a pre-built ERP more than allowed "scaffolding" | Medium | High (disqualification) | Ask organisers now (P0-02); strict PRE/EVENT split; full disclosure; AI layer clearly built during the event | Owner |
| R2 | **Scope:** production-grade ERP + AI in ~6 weeks, MVP in 15 days | High | High | Phase gates; MVP module list fixed; v1 modules (sales, purchasing, files) deferred to P8; cut list below | Owner |
| R3 | **Generated code drifts from the spec** as many agents work in parallel | High | Medium | Architecture tests, generated API types, module cards, one-module tasks, Tier-A review at gates | Tier-A reviewer |
| R4 | **Integration bugs** in concurrency, time zones and transactions | Medium | High | Database constraints (exclusion, checks), concurrency tests, time zone eval cases, Postgres in all tests | Tier A |
| R5 | **AI quality:** cheap models produce invalid or wrong proposals | Medium | Medium | JSON-schema protocol, repair step, eval-driven model choice, human approval always | P5 |
| R6 | **Keycloak complexity** slows P2 | Medium | Medium | Realm-as-code, a dedicated Tier-A task, test fixtures bypassing OIDC for API tests; fallback: deliver P2 login with Keycloak defaults and polish the theme in P8 | Tier A |
| R7 | **Credits only arrive at kickoff;** first cloud deploy problems eat event time | Medium | Medium | Everything tested locally in compose; install test the morning of 3 Nov; small, scripted deploy | Owner |
| R8 | **Hosted inference privacy** conflicts with the "customer-owned" pitch | Medium | Medium | Clear `ai_policy` setting; strict-private option; honest wording in the demo | Owner |
| R9 | **Budget overrun** | Low | Medium | Budget table, alerts, hourly-only benchmarks, daily token caps | Owner |
| R10 | **Single VM** is a single point of failure for customers | Medium | Medium (SMB-acceptable) | Backups + tested restore, documented recovery time; profile M later | P8 |
| R11 | **Vultr catalog changes** (models removed or repriced) | Medium | Low | Routes are configuration; fallback chain; P5-01 probe re-reads the catalog | P5 |

## Cut list (in order) if the hackathon schedule slips

1. Voice (M14), the first to go.
2. Knowledge-base answers (U8).
3. Marketing (M12): keep a draft-only demo or drop it.
4. Daily AI briefing (U6).
5. Reorder narrative (U4): the deterministic suggestion stays.
6. Public chat widget UI polish: staff-logged messages can trigger intake instead.

**Never cut:** login and roles, cross-business isolation, approvals binding, audit, booking conflict handling, deployment on a Vultr VM.

## Open questions

| # | Question | Needed by | Default if unanswered | Status |
|---|---|---|---|---|
| Q1 | Do the organisers accept a non-AI ERP base built before kickoff (disclosed)? | P1 start (Nov 3) | Proceed with disclosure per ADR-014; present the AI layer as the event work | ANSWERED 2026-11-03 by the owner: lablab allows building locally before kickoff. ADR-014 split (AI after kickoff) kept as the safe default |
| Q2 | Is the owner competing solo or with a team? Who reviews Tier-A work? | P1 | Owner reviews; second model review | OPEN |
| Q3 | Domain name for the demo (needed for HTTPS) | P4 (Nov 3) | Owner buys a cheap domain; fallback `<ip>.sslip.io` (rate limits may apply) | OPEN |
| Q4 | Approve installing local tooling: Docker Desktop or OrbStack, `uv`, Node 22 + pnpm, Terraform, (optional) `gitleaks`, `trivy` | P1-01 | Nothing is installed without approval | ANSWERED 2026-11-03: owner approved installing any needed tools. Installed: pnpm 12.15 (Corepack), Terraform 1.17, gitleaks 8.30. Already present: Docker 29.8, uv 0.12, Node 24, Python 3.13 |
| Q5 | Allow local git commits from P1 (recommended, for honest dated history)? | P1-01 | No commits (current AGENTS.md) | ANSWERED 2026-11-03: **no commits** until the owner says otherwise |
| Q6 | Demo business vertical confirmed as bike repair shop? | P3 seed | Yes (fixtures already use it) | PROPOSED |
| Q7 | SMTP provider for v1 email (customer-chosen, or a recommended default)? | P8 | Simulated only until decided. 2026-11-04 recommendation: keep simulated until the owner's own domain exists (no service can send properly from `sslip.io`: SPF/DKIM/DMARC need a domain); then an SMTP relay on port 587 (for example Amazon SES, Postmark, Brevo, Mailgun) rather than a self-hosted server (Vultr blocks outbound port 25 by default; new IP reputation). Self-hosted option if wanted later: Stalwart (AGPL-3.0). Optional for testing: Mailpit on the demo VM (new container, needs owner approval) | OPEN |
| Q8 | Publish images publicly (simplifies customer installs) or keep them private? | P7 | Private registry with read-only tokens | OPEN |
| Q9 | Which external model providers may be used at the event if Vultr models fail evals? | P5-03 | Vultr only unless the owner adds keys | OPEN |
| Q10 | Healthcare (for example dental clinics) as a later vertical? Checked 2026-11-03 from public pages: Vultr's Terms of Service §17 offer a "Configurable HIPAA Server" (virtual private servers only) after an architectural review and a signed acknowledgement; the ToS do not mention a BAA, Object Storage, Managed Databases or Serverless Inference, and say Vultr provides no backups for those servers. Vultr's inference docs say prompts are processed in memory and not stored by default, with no HIPAA statement. OpenAI (API, with modified retention) and Anthropic (first-party API, selected features) state that they sign BAAs; Google offers Vertex AI under its Cloud BAA. | Post-v1 | Keep healthcare out of scope for v1 and the event. Design for it by configuration: [ADR-018](03-decisions.md#adr-018-regulated-data-profile-health-clinics-later-by-configuration) (regulated data profile). Before any clinic edition: written confirmation from Vultr of which products a BAA covers, a BAA-covered inference route, read-access audit logging, retention vs erasure rules, legal review | OPEN |

Agents append new questions here with the next `Q` number, the task ID that raised them and status `OPEN`.
