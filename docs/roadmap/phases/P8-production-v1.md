# P8 · Production v1 (Nov 9 – Dec 20)

**Goal:** turn the hackathon MVP into a product that can be installed for a real customer: the remaining ERP modules, reliability, security and operations. Exit = the [production readiness checklist](../11-quality-and-operations.md#production-readiness-checklist-p8-exit-gate-before-any-real-customer-data).

Tasks below are specified at the same level as P1–P5 when P8 starts (task P8-01 writes the detailed blocks into this file from the module specs). Sizes are estimates.

| ID | Task | Module | Tier | Size | Depends on |
|---|---|---|---|---|---|
| P8-01 | Retrospective; update roadmap, ADRs and risks with event learnings; detail the P8 task blocks | docs | A + owner | M | P7 |
| P8-02 | SMTP adapter, email templates, Keycloak SMTP for invitations and password reset | M10, M01 | A | M | P8-01 |
| P8-03 | files module (Object Storage, pre-signed URLs) | M15 | B | M | P8-01 |
| P8-04 | sales module: quotes, orders, invoices, numbering, reservations | M16 | A/B | XL (split into 4) | P8-01 |
| P8-05 | purchasing: suppliers, POs, receipts, reorder draft conversion | M17 | B | L | P8-04 |
| P8-06 | admin_ops: full System page, export, erasure, retention jobs | M18 | A | L | P8-03 |
| P8-07 | pgBackRest point-in-time recovery to Object Storage + restore drill | deploy | A | M | P8-01 |
| P8-08 | Audit hash chain + nightly export | M00/M09 | A | S | P8-01 |
| P8-09 | Host-level egress filtering (`DOCKER-USER` chain) + test | deploy | A | M | P8-01 |
| P8-10 | Optional second approver per action type | M08 | A | M | P8-01 |
| P8-11 | Customer merge (preview, merge, event) | M03 | B | M | P8-01 |
| P8-12 | Email ingestion for conversations (IMAP or provider webhook, `source_ref` dedupe) | M04 | A | L | P8-02 |
| P8-13 | Profile M: Managed PostgreSQL + VPC option in Terraform (verify extensions first) | deploy | A | M | P8-07 |
| P8-14 | Load test and performance fixes (targets in [11](../11-quality-and-operations.md#test-strategy)) | all | A | M | P8-04 |
| P8-15 | Security review (OWASP ASVS L2 subset) and fixes | all | A | L | P8-06 |
| P8-16 | Accessibility audit and fixes | frontend | B | M | P8-04 |
| P8-17 | Installer hardening; fresh install on `vhp-2c-4gb-amd`; target ≤ 30 minutes | deploy | A | M | P8-07 |
| P8-18 | Upgrade-path test from v0.1.0 with data | deploy | A | S | P8-17 |
| P8-19 | Customer user guide and admin guide | docs | B | M | P8-06 |
| P8-20 | CI on GitHub Actions (after owner authorisation): `make check`, `make e2e`, image build, push to Vultr Container Registry | repo | A | M | P7-01 |
| P8-21 | Voice v1 (if not done at the event) | M14 | A | L | P8-01 |
| P8-22 | Knowledge-base answers (embeddings with pgvector) | M13 | A | L | P8-01 |
| P8-23 | Regulated data profile, non-AI half ([ADR-018](../03-decisions.md#adr-018-regulated-data-profile-health-clinics-later-by-configuration)): `DATA_PROFILE`, startup attestation check, read-access audit, retention hold, minimal templates, chat notice, session lifetime, per-business terminology overrides, System page display, runbook | platform, M01, M09, M10, M18, frontend | A | M | — (PRE; may run before kickoff) |

## Exit gate P8
- [ ] Every item in the production readiness checklist is checked or has a written, owner-accepted exception.
- [ ] Version `1.0.0` tagged.
