# P4 · Packaging and deployment (Nov 3) — PRE, then DEPLOY on Nov 3

**Goal:** production images, production compose, backups and the Vultr installer, all rehearsed locally before credits exist. Then a short, scripted first deployment on 3 Nov.
**Entry:** P3-14 done (seed works). P4-01 … P4-08 may start as soon as P2 is done.

### P4-01 · Production images
Kind: PRE · Tier: A · Module: deploy · Size: M · Depends on: P1-13
Read first: [10 Container images](../10-vultr-infrastructure.md#container-images)
Create: `deploy/docker/{api,web,backup}.Dockerfile`, `.dockerignore`, `make images VERSION=0.1.0`.
Done when:
- [ ] Images build; `smb-api` runs as non-root with a read-only root FS; image sizes recorded (target: api < 250 MB, web < 60 MB).
- [ ] `trivy image` (if installed) shows no critical vulnerabilities, or exceptions are recorded.

### P4-02 · Production Compose, Caddy, Postgres init, Keycloak production config
Kind: PRE · Tier: A · Module: deploy · Size: M · Depends on: P4-01
Read first: [10 compose spec](../10-vultr-infrastructure.md#composeprodyml-specification), [06 Network exposure](../06-security-and-privacy.md#network-exposure)
Create: `deploy/compose/compose.prod.yml`, `deploy/compose/Caddyfile`, production realm template (no test users, redirect URIs from `$DOMAIN`).
Done when:
- [ ] `docker compose -f compose.prod.yml config` validates; only `caddy` has `ports`; `/auth/admin` returns 404 through Caddy.

### P4-03 · Backup and restore
Kind: PRE · Tier: A · Module: deploy · Size: M · Depends on: P4-01
Read first: [10 Backups](../10-vultr-infrastructure.md#backups-and-restore), [ADR-010](../03-decisions.md#adr-010-backups-restic-to-vultr-object-storage-point-in-time-recovery-in-v1)
Create: `deploy/scripts/{backup.sh,restore.sh}`, backup crontab, status JSON, `make backup-test` (uses a local MinIO **or** a local restic file repository; no cloud).
Done when:
- [ ] `make backup-test` restores into a scratch DB with identical row counts per table and records the duration.

### P4-04 · Terraform module and demo environment
Kind: PRE · Tier: A · Module: deploy · Size: M · Depends on: —
Read first: [10 Terraform](../10-vultr-infrastructure.md#terraform-module-deployterraformmodulesvultr-single-vm), [14 sources](../14-sources.md#vultr-platform)
Create: `deploy/terraform/modules/vultr-single-vm/{main,variables,outputs,versions}.tf`, `deploy/terraform/envs/demo/{main.tf,terraform.tfvars.example}`.
Done when:
- [ ] `terraform init -backend=false && terraform validate` passes (no API key needed). **No `plan` or `apply` in P4-04.**

### P4-05 · cloud-init template
Kind: PRE · Tier: A · Module: deploy · Size: S · Depends on: P4-04
Done when:
- [ ] `cloud-init schema --config-file` validates the rendered template (or a YAML lint if cloud-init is not installed locally; note which was used). It contains no secrets.

### P4-06 · install, upgrade and smoke scripts
Kind: PRE · Tier: A · Module: deploy · Size: L · Depends on: P4-02, P4-03, P4-05
Read first: [10 Install flow](../10-vultr-infrastructure.md#install-flow-deployscriptsinstallsh--customer-or-demo), [10 Upgrades](../10-vultr-infrastructure.md#upgrades-and-rollback-deployscriptsupgradesh-version)
Done when:
- [ ] `shellcheck` passes (if installed); `install.sh --dry-run` prints every step without calling Vultr; `smoke.sh https://localhost:8443` passes against the local production rehearsal.

### P4-07 · Local production rehearsal
Kind: PRE · Tier: B · Module: deploy · Size: M · Depends on: P4-06
Steps: run `compose.prod.yml` locally with `DOMAIN=localhost` and Caddy internal TLS, bootstrap a business, seed, run `smoke.sh` and the e2e suite against it, then run `upgrade.sh` from 0.1.0-rc1 to 0.1.0-rc2.
Done when:
- [ ] e2e green against the production images; upgrade rehearsal recorded.

### P4-08 · Runbooks
Kind: PRE · Tier: B · Module: docs · Size: M · Depends on: P4-06
Create: `docs/runbooks/{install,upgrade,backup-restore,rotate-secrets,incident,teardown,access}.md` per [11](../11-quality-and-operations.md#runbooks-written-in-p4p8-under-docsrunbooks).
Done when:
- [ ] A different model, given only `install.md`, can describe each step without asking questions (review prompt recorded).

### P4-09 · First Vultr deployment (demo)
Kind: **DEPLOY** · Tier: A · Owner approval required in chat · Size: M · Depends on: P4-07, P0-02, kickoff credits
Steps (owner present):
1. Owner redeems the credit code on a new Vultr account (new email) and sets billing alerts ($100, $150).
2. Owner creates in the console: Container Registry (Start Up), an Object Storage subscription (Standard, `sjc`) with buckets `smb-backups` and `smb-files`, Serverless Inference, and API keys. Keys go straight into the operator's local `install-demo/.env`, never into chat or the repo.
3. `make images VERSION=0.1.0` → push to the registry.
4. `install.sh` for `envs/demo` (`vhp-4c-8gb-amd`, `sjc`) → DNS → bootstrap → seed demo data → smoke.
5. External port scan (IPv4 and IPv6) from another host; record the results.

Done when:
- [ ] The demo URL serves the app over HTTPS; login works; seed visible; scan shows only 80/443 (+22 if allowed); first backup in Object Storage; spend so far recorded.

## Exit gate P4
- [ ] Local production rehearsal passed (before kickoff).
- [ ] After kickoff: demo live on Vultr (P4-09) with the smoke test passing and a backup taken.
