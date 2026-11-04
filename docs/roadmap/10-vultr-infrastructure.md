# 10 · Vultr infrastructure, packaging and deployment

## Vultr services we use

| Vultr service | Used for | Profile | Verified (2026-11-03, [15](14-sources.md)) |
|---|---|---|---|
| **Cloud Compute** (High Performance AMD, `vhp-*`) | The VM running Docker Compose | All | Plan IDs and prices from the public `/v2/plans` API |
| **Firewall Groups** | Inbound 80/443 (+22 restricted) | All | Ingress only; egress is controlled on the host and app ([06](06-security-and-privacy.md#egress)) |
| **Serverless Inference** | LLM, safety, embeddings, STT/TTS | All, unless `strict_private` | Public model list at `api.vultrinference.com/v1/models`; token-billed |
| **Object Storage** (S3-compatible) | Encrypted backups (restic), file attachments (v1) | All | Clusters include `sjc1.vultrobjects.com`; Standard tier $18/month for 1 TB storage + 1 TB transfer (docs) |
| **Container Registry** | Our images | Build/publish | Start Up plan free with 10 GB (docs) |
| **DNS** (optional) | Customer domain records | Optional | Customers may use their own DNS instead |
| **VPC** | Private network between app VM and managed DB | Profile M only | Documented for Cloud Compute and Managed Databases |
| **Managed Databases for PostgreSQL** | Customers who prefer a managed DB | Profile M (optional) | **Price not verified.** A third-party source says the entry tier is about $90/month. Check `btree_gist` and `vector` extension support first |
| **Cloud Compute, short-lived** | Local-LLM CPU benchmark (P5-02), install tests | Temporary | Hourly billing per plan |
| Load Balancer, Kubernetes Engine, Cloud GPU | Not used ([ADR-008](03-decisions.md#adr-008-docker-compose-on-a-vm-not-kubernetes)); GPUs unavailable at the event | — | — |

**Region:** `sjc` (Silicon Valley). It has every Compute plan we need and an Object Storage cluster (`sjc1`), and it is close to the inference data centres listed (`sjc`, `sea`). The owner may choose another region per customer. The Terraform module takes `region` as input.

## Deployment profiles

| Profile | Who | Topology | Minimum plan |
|---|---|---|---|
| **S — Single VM** (default) | Most SMBs, our demo | One VM: caddy, api, worker, keycloak, postgres, backup. Object Storage for backups | `vhp-2c-4gb-amd` ($24/month) minimum; `vhp-4c-8gb-amd` ($48/month) recommended |
| **S+P — Single VM, strict-private AI** | Privacy-first customers | Profile S plus a `llm-local` container (llama.cpp) using the compose profile `private` | `vhp-8c-16gb-amd` ($96/month) or larger; feasibility decided by P5-02 |
| **M — Managed DB** (v1) | Larger customers | App VM + Vultr Managed PostgreSQL in a VPC; VM keeps no state except Caddy certificates | App `vhp-2c-4gb-amd` + managed DB |

## Environments

| Environment | Where | Data | When |
|---|---|---|---|
| `dev` | Each developer's machine (Docker Desktop or OrbStack) | Seeded synthetic data | From P1 |
| `test` | Same compose, `db-test` service; later CI | Ephemeral | From P1 |
| `demo` | Vultr, our hackathon account (`deploy/terraform/envs/demo`) | Synthetic only | **From kickoff (3 Nov)**, when credits are redeemed |
| `customer-<name>` | Customer's own Vultr account | Real (pilot) | P9, after the production gate |

**Credits timing:** the $200 credit gift code is shared **at kickoff** and must be redeemed on a Vultr account with an email not previously linked to Vultr. Until then nothing runs on Vultr unless the owner explicitly approves paying personally. P4 builds and tests everything locally, so first deployment is a short task on 3 Nov.

## Container images

| Image | Base | Contents | Runs as |
|---|---|---|---|
| `smb-api:<version>` | `python:3.13-slim` (pinned digest), multi-stage with `uv sync --frozen --no-dev` | Backend app, Alembic, CLI | Non-root `app` user, read-only root filesystem, `/tmp` tmpfs |
| `smb-web:<version>` | Build stage `node:24-alpine` → runtime `caddy:<pinned>` | SPA `dist/` + `Caddyfile` | Non-root user `caddy` (uid 10003); Compose adds a read-only root filesystem, `cap_drop: [ALL]` with only `NET_BIND_SERVICE` added back, and `no-new-privileges` |
| `smb-backup:<version>` | `postgres:17-alpine` client tools + `restic` + `supercronic` | `backup.sh`, `restore.sh`, crontab | Non-root |
| Third-party (pinned by digest) | `pgvector/pgvector:pg17`, `quay.io/keycloak/keycloak:<pinned>`, optional `ghcr.io/ggml-org/llama.cpp:server` | — | — |

Version tags follow semver (`0.1.0`), plus the git SHA once commits exist. Images go to Vultr Container Registry `<registry>.vultrcr.com/smb/…` (exact hostname from the console when the registry is created). Customer installs pull with a **read-only** registry credential, or from a public registry if the owner decides to publish images at P7 or later.

## `compose.prod.yml` (specification)

| Service | Image | Ports | Depends on | Health check | Memory limit |
|---|---|---|---|---|---|
| `caddy` | `smb-web` | **80, 443 (only published ports)** | api, keycloak | `caddy validate` + HTTP on `/` | 128 MB |
| `api` | `smb-api` (`uvicorn app.main:create_app --factory --workers 2 --proxy-headers`) | none | migrate (completed), postgres | `GET /readyz` | 768 MB |
| `worker` | `smb-api` (`python -m app.worker`) | none | migrate, postgres | heartbeat file age < 30 s | 512 MB |
| `migrate` | `smb-api` (`alembic upgrade head`), one-shot | none | postgres | exit code | 256 MB |
| `postgres` | `pgvector/pgvector:pg17` | none | — | `pg_isready` | 1 GB (shared_buffers 256 MB) |
| `keycloak` | Keycloak pinned (`start --import-realm --http-relative-path /auth --proxy-headers xforwarded --hostname https://$DOMAIN/auth`) | none | postgres | `/auth/health/ready` (management port) | 768 MB |
| `backup` | `smb-backup` | none | postgres | last-success file age < 26 h | 256 MB |
| `llm-local` *(profile `private`)* | llama.cpp server | none | — | `/health` | set from model size |

All services: `restart: unless-stopped`, `json-file` logs capped at 10 MB × 5, and a single internal network. Volumes: `pgdata`, `caddy_data`, `caddy_config`, `backup_state`. Files under `/opt/smb/`: `compose.yml`, `.env` (600), `ai-routes.yaml`, `VERSION`.

**Container hardening.** Every service sets `cap_drop: [ALL]` and `no-new-privileges`, and the main process of no running container is root: `api`, `worker` and `migrate` run as uid 10001 with a read-only root filesystem and a `/tmp` tmpfs; `backup` as 10002; `caddy` as 10003 with a read-only root filesystem; `postgres` is started as its own user (`user: "999:999"`, no root phase); Keycloak's image runs as 1000. Only `caddy` keeps a capability, `NET_BIND_SERVICE`, and only because the upstream `caddy` binary carries it as a file capability (`cap_net_bind_service=ep`): with `cap_drop: [ALL]` and nothing added back, the kernel refuses to start the binary ("operation not permitted"). Docker already lets any user bind ports 80 and 443 inside a container, so the ports stay 80 and 443 and Caddy's automatic HTTPS (redirect, ACME challenges) is unchanged; the alternative, high ports inside and a port mapping, would make Caddy redirect to its internal HTTPS port and need its own redirect and challenge settings. `caddy` writes only to the volumes `caddy_data` and `caddy_config`, which belong to uid 10003; volumes of an installation made earlier belong to root and `upgrade.sh` hands them over ([upgrade runbook](../runbooks/upgrade.md)). `make deploy-check` and `smoke.sh` fail if any of this is lost.

**Caddyfile (outline):** `{$DOMAIN}` → `encode zstd gzip`; security headers ([06](06-security-and-privacy.md#network-exposure)); `handle /api/* { reverse_proxy api:8000 }`; `handle /auth/admin* { respond 404 }`; `handle /auth/* { reverse_proxy keycloak:8080 }`; `handle { root * /srv; try_files {path} /index.html; file_server }`. Automatic HTTPS needs a DNS name pointing at the VM.

## Terraform module `deploy/terraform/modules/vultr-single-vm`

Uses the official provider `vultr/vultr`, with its version pinned in `versions.tf`. Check resource arguments against the provider reference at implementation time ([15](14-sources.md)).

| Inputs | Purpose |
|---|---|
| `name`, `region` (default `sjc`), `plan` (default `vhp-4c-8gb-amd`), `os_id` (Ubuntu 24.04 LTS id from `/v2/os`) | Instance |
| `ssh_public_key` | Installer's per-install key |
| `admin_cidrs` (list, default `[]`) | SSH allowed sources (IPv4/IPv6) |
| `enable_backups` (bool, default false) | Vultr automatic VM backups (extra cost) in addition to restic |
| `tags` | Labelling |

| Resources | Notes |
|---|---|
| `vultr_ssh_key` | |
| `vultr_firewall_group` + `vultr_firewall_rule` ×N | 80/443 v4+v6 from anywhere; 22 from `admin_cidrs` |
| `vultr_instance` | `user_data` = cloud-init **without secrets**; `enable_ipv6 = true`; `firewall_group_id` |
| Outputs | `ipv4`, `ipv6`, `instance_id` |

Object Storage buckets and registry are created once per account by `install.sh` through the Vultr API or console instructions. Terraform support for them can be added later. **State** for `envs/demo` stays local (`terraform.tfstate`, gitignored) because no remote state backend is authorised.

**cloud-init (`user-data.yaml.tftpl`)** contains no secrets:
1. Update packages; enable `unattended-upgrades`.
2. Install Docker Engine and the Compose plugin from Docker's official apt repository.
3. Create the `smb` user (docker group) and `/opt/smb`.
4. `ufw` default deny incoming; allow 22/80/443. This is redundant with the firewall group, which is intended.
5. Install the systemd unit `smb.service` (`docker compose -f /opt/smb/compose.yml up -d`, `ExecStop` down).
6. Write `/opt/smb/READY` when finished.

## Install flow (`deploy/scripts/install.sh`) — customer or demo

```text
1. Preflight: terraform, ssh, curl, openssl present; ask for: Vultr API key (env only, not saved), domain, region, plan,
   owner email, business name/slug/timezone, AI policy and inference key (optional).
2. Generate secrets locally → ./install-<name>/.env (600) and print RESTIC_PASSWORD once for offline storage.
3. terraform apply (module above) → IP addresses.
4. Ask the owner to create DNS A/AAAA records → wait until they resolve.
5. Wait for /opt/smb/READY over SSH; upload compose.yml, .env, ai-routes.yaml; docker login (read-only registry token); docker compose pull; up -d.
6. Wait for https://DOMAIN/api/v1/readyz; run `bootstrap-business` (creates owner Keycloak user + business).
7. Run smoke.sh (login page, API health, backup dry-run to Object Storage); print the owner's first-login instructions.
8. Optionally remove the SSH firewall rule (admin uses Vultr console or re-adds their IP when needed).
```

## Upgrades and rollback (`deploy/scripts/upgrade.sh <version>`)

1. Run a backup now and wait for success.
2. `docker compose pull` with the new tag → `docker compose run --rm migrate` → `docker compose up -d`.
3. Run `smoke.sh`. If it fails, set the previous tag and run `up -d`. This is safe because migrations are expand/contract ([04](04-repository-and-conventions.md#migrations)). If the data itself is damaged, follow the restore runbook.
4. Record the version and result in `/opt/smb/upgrade.log`. The System page shows it.

## Backups and restore

- `backup` container schedule: `pg_dump -Fc` of the `smb` and `keycloak` databases every 6 h and nightly → `restic backup` → `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune` (weekly) → `restic check --read-data-subset=5%` (weekly). It writes a status JSON that the System page reads.
- `restore.sh <snapshot>`: stop api and worker, restore into a new database, verify row counts and run `alembic current`, swap database names, start, smoke test.
- **Restore drill:** `make backup-test` locally in CI. On the demo VM, P6-08 restores into a scratch database and records the time taken (the recovery-time measurement). Recovery-point objective with the MVP schedule: ≤ 6 h. With pgBackRest in v1: ≤ 5 minutes.

## Teardown (demo, after the event or when credits run low)

`terraform destroy` in `envs/demo` → delete Object Storage subscription and registry if no longer needed → check the Vultr billing page shows no running resources → record the date and result in `PRE_EVENT_WORK.md` / project log.

## Budget ($200 credits)

Prices are from Vultr's public plan API and docs on 2026-11-03. Re-check at redemption.

| Item | Plan | Monthly | Notes |
|---|---|---|---|
| Demo VM | `vhp-4c-8gb-amd` (4 vCPU, 8 GB, 180 GB) | $48.00 | $0.066/h |
| Object Storage | Standard | $18.00 | Backups (+ files in v1) |
| Container Registry | Start Up | $0.00 | 10 GB |
| Serverless Inference | Usage | ~$5–15 | Estimate in [08](08-ai-layer.md#cost-estimate-for-budgeting-to-be-replaced-by-measurements); hard daily caps in the app |
| **Recurring total** | | **≈ $71–81** | |
| One-off: CPU LLM benchmark | `vhp-8c-16gb-amd` × ≤ 4 h | ≈ $0.55 | Destroy immediately |
| One-off: installer test on minimum size | `vhp-2c-4gb-amd` × ≤ 6 h | ≈ $0.20 | Destroy immediately |

**Runway:** about 2.5 months from 3 Nov, so roughly mid-January 2027, which covers the event and P8. **Guardrails:** set Vultr billing alerts at $100 and $150 (provider alerts are notifications, not hard caps). The owner reviews spend weekly. Nothing larger than `vhp-4c-8gb-amd` runs for more than 4 hours without owner approval. GPU instances are not used. Confirm the credit expiry date when redeeming.

## As built (P4, 2026-11-03)

Files: `deploy/docker/{api,web,backup}.Dockerfile` (build context = repository root, `.dockerignore` at the root), `deploy/compose/{compose.prod.yml,Caddyfile,env.prod.template,realm-smb.prod.json,compose.backup-test.yml}`, `deploy/scripts/{gen-env,stage,install,upgrade,restore,smoke,backup,rehearse,backup-test,check-deploy}.sh`, `deploy/scripts/lib/restore-db.sh`, `deploy/scripts/backup.crontab`, `deploy/terraform/{modules/vultr-single-vm,envs/demo}`. Make targets: `images`, `backup-test`, `deploy-check` (part of `make check`), `rehearse`.

**Deviations from the plan above, and why**
- **Layout on the VM** is flat (`/opt/smb/{compose.yml,.env,postgres-init.sh,realm-smb.prod.json,smoke.sh,upgrade.sh,restore.sh}`), assembled by `stage.sh`. The production realm template therefore lives in `deploy/compose/`, not `deploy/keycloak/`.
- **Compose project name** is fixed `smb-prod` so a local rehearsal never touches the dev stack's volumes (`smb`).
- **Least privilege:** `api` and `worker` get only the application database role; only `migrate` gets the owner role. `seed --reset` on a production stack needs `DATABASE_MIGRATION_URL` passed explicitly.
- **Keycloak** runs with `KC_CACHE=local` (no clustering). Without it, a restart after a crash waited minutes trying to join the dead node left in the database. The realm import fails on unknown fields such as `_comment`.
- **Caddy** serves the widget for `/p/*` (rewrite to `widget.html`) and sends our Content-Security-Policy only for the app and widget routes (Keycloak sets its own). The container health check uses an internal-only listener on `127.0.0.1:8081`.
- **Backup** container: scripts are `backup.sh` (in the image) and `lib/restore-db.sh` (in the image, run as the PostgreSQL superuser); `deploy/scripts/restore.sh` is the host-side orchestrator that stops the stack, swaps databases and runs the smoke test. The first backup runs at container start if the last one is older than 6 h. supercronic v0.2.51 is downloaded with a pinned SHA-256 per architecture. The unused `gosu` binary from the base image is removed (it carried the only critical vulnerability).
- **`PLATFORM`:** `make images` builds for the local machine by default; for a Vultr VM use `PLATFORM=linux/amd64`. This was **not** built or tested on amd64 (the rehearsal ran on arm64).
- **Local rehearsal** uses HTTP port 8080 and HTTPS 8443, and `APP_ENV=dev` only while the end-to-end tests run (the dev-only `dev-account` helper refuses `prod`).
- **Web container hardening (2026-11-03, F4 in [the re-scan](../security/2026-11-03-rescan.md)):** `smb-web` has a `USER` (the `caddy` user, uid 10003, also set as `user:` in Compose), a read-only root filesystem, no capability but `NET_BIND_SERVICE`, and `no-new-privileges`. Caddy's own HTTP-to-HTTPS redirect is switched off (`auto_https disable_redirects`) and replaced by an `http://` site, so that no response of the proxy says `Server: Caddy` (F7); `skip_install_trust` keeps the internal CA of the local rehearsal from trying to write the read-only trust store. PostgreSQL starts as `999:999` and Keycloak drops its capabilities as well.

**Measured**
| Item | Result |
|---|---|
| Image sizes (on disk / compressed) | api 269 MB / 80 MB; web 64.6 MB / 23 MB; backup 358 MB / about 134 MB. The targets (api < 250 MB, web < 60 MB) are missed by 19 MB and 5 MB: they are Python and Caddy base images plus dependencies. Accepted. |
| `smb-api` runtime | Non-root (uid 10001), read-only root filesystem with `/tmp` tmpfs, no pip, capabilities dropped |
| Process user and capabilities (`/proc/1/status` of each container, full production Compose stack on one machine, 2026-11-03) | caddy 10003, `CapEff` 0x400 (`NET_BIND_SERVICE`); postgres 999, api and worker 10001, backup 10002, keycloak 1000: `CapEff` and `CapBnd` 0; all with `NoNewPrivs` 1. An older image and an older `compose.yml` both still work (rollback), and an existing root-owned `caddy_data` keeps its certificate after `upgrade.sh` |
| trivy (critical and high, fixable) | api 0; backup 0 (after removing gosu); web 6 HIGH, all in the upstream Caddy 2.11.7 binary (Go standard library, fixed in newer Go) and Alpine `brotli-libs`. Caddy 2.11.7 is the newest release on 2026-11-03. **Exception recorded; re-scan before P4-09.** |
| `make backup-test` | PASS: 48 tables, 234 rows restored with identical counts; backup 5 s, restore 1–2 s; a negative control proves a difference is detected; the application role reads tenant rows through row-level security in the restored database |
| `make rehearse` | PASS in 181 s: stack up, bootstrap, seed, smoke, 12 end-to-end tests on the production images, upgrade rc1→rc2, failed upgrade (missing image) rolled back, restore drill and real restore (44 s including smoke) |
| Not exercised | `install.sh` against Vultr (dry run only), Terraform `plan`/`apply`, cloud-init on a real VM, restic against Object Storage, Let's Encrypt, amd64 images |

**Open items:** SMTP for invitation e-mails; the first `docker push` to Vultr Container Registry.

**Resolved (2026-11-03): how the demo owner signs in to the seeded businesses on a real install.** The seed creates staff that claim a login on first sign-in, but Keycloak accounts existed only for people created by `bootstrap-business`, and `dev-account` is dev-only. `python -m app.cli demo-logins` now creates the Keycloak accounts for exactly the demo staff of `data/fixtures.json` on any install: random passwords in a mode-600 file (never printed), permanent by default with the authenticator app still mandatory, idempotent, refusing every other business and the regulated profile. Procedure: [install.md](../runbooks/install.md#demo-installs-only-logins-for-the-demo-staff-morgan-riley-and-the-others); card: [M01 identity](modules/M01-identity.md).

**Review (2026-11-03, Codex):** 15 findings fixed (see `PRE_EVENT_WORK.md`). Behaviour changes worth knowing: `install.sh` now requires `--admin-cidr`; `restore.sh latest` pins the newest snapshot id before its safety backup; `restore-db.sh` exits 10 when row counts differ and refuses to target `smb`, `keycloak`, `postgres` or template databases; rollback in `upgrade.sh` starts every service except `migrate` with `--no-deps` (the old image cannot run a migration the new image already applied); the production `postgres-init.sh` reads passwords with `\getenv`. `make rehearse` now includes a rollback after an applied migration.

**Follow-up (2026-11-03):** the Keycloak container is healthy only when the realm `smb` answers (not when the management endpoint is ready); `restore.sh` runs `migrate` on restored data before starting the application; `upgrade.sh` rolls back on interruption; `access.sh open|close` manages the SSH firewall rule; `make rehearse` now proves rollback after an applied migration, and its negative control was run (the old rollback code fails that step).

**amd64 (2026-11-03):** `make images PLATFORM=linux/amd64` builds all three images; the api image is 238 MB (under the 250 MB target; the arm64 build is 269 MB because of different wheels), web 67.3 MB, backup 367 MB. They were started under emulation (`python` imports, `restic`, `supercronic`, `caddy`). The full rehearsal has not been run on amd64.
