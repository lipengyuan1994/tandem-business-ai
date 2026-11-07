# Vultr spend log

Hard limit: **$200 of credit for the whole project** (owner, 2026-11-03). Every resource that bills is listed here when it
is created and when it is destroyed. Prices are Vultr list prices; Vultr bills hourly up to the monthly cap.

Check the balance at any time: `GET https://api.vultr.com/v2/account` (`balance` is negative while credit remains;
`pending_charges` is this month's spend so far).

## Budget plan

| Item | Price | Plan |
|---|---|---|
| Serverless Inference | token-billed (subscription details in the Vultr console) | Probe, evals, demo traffic: expected well under $20 |
| Demo VM `vhp-2c-4gb-amd` | $24/month (≈ $0.036/hour) | Test deploy; keep for the demo only if the owner wants it running |
| Container Registry | Start Up plan: free (10 GB) | Images are about 0.7 GB per version |
| Object Storage | $18/month (≈ $0.025/hour) | Needed for backups; destroy after the backup and restore test unless the demo keeps running |

## Ledger

| UTC time | Action | Resource | Running cost | Balance after (credit left) |
|---|---|---|---|---|
| 2026-11-03 | Credit applied | — | — | $200.00, pending $0.00 (checked via API) |
| 2026-11-03 02:02 | Created | Container Registry `smbopsrqk49` (`sjc.vultrcr.com/smbopsrqk49`, private, Start Up plan) | $0.00/month | $200.00 |
| 2026-11-03 02:40 | Pushed | Images `smb-api`, `smb-web`, `smb-backup` 0.1.0 (≈ 0.7 GB) to the registry | $0.00 | $200.00 |
| 2026-11-03 02:41 | Created | Object Storage `smb-demo-backups` (sjc, Standard tier, id `a3b79260…`) | $18/month ≈ $0.025/hour | — |
| 2026-11-03 02:50 | Creating | VM `smb-demo` (vhp-2c-4gb-amd, sjc) + firewall group + SSH key via install.sh --demo | $24/month ≈ $0.036/hour | — |
| 2026-11-03 03:05 | Created (by install.sh) | VM `smb-demo` 45.63.85.83, `https://45-63-85-83.sslip.io` (vhp-2c-4gb-amd, sjc), firewall group (80/443 open, 22 only for the admin address), SSH key | $24/month ≈ $0.036/hour | see next row |
| 2026-11-03 03:20 | Checked | Vultr account | — | pending charges $0.07 of $200.00 |
| 2026-11-03 04:05 | Upgraded | VM to 0.1.1 with `upgrade.sh` (images 0.1.1 pushed, ≈ 0.7 GB more in the registry) | — | — |
| 2026-11-03 04:10 | Checked | Vultr account after the first live intake on the VM | — | pending charges $0.14 of $200.00 |
| 2026-11-04 05:20 | Upgraded | VM to 0.1.2 (images 0.1.2 pushed; registry now holds 3 versions ≈ 2 GB of the free 10 GB) | — | pending charges $0.2 of $200.00 |
| 2026-11-04 06:30 | Upgraded | VM to 0.1.3 (images 0.1.3 pushed; registry ≈ 2.7 GB of 10 GB free tier) | — | pending charges $1.49 of $200.00 |
| 2026-11-04 07:21 | Upgraded | VM to 0.1.4 (registry holds 0.1.0–0.1.4 ≈ 3.4 GB of the free 10 GB; delete old tags when space matters) | — | — |
| 2026-11-04 07:35 | Closed | SSH rule removed from the VM's firewall (targeted Terraform apply); only 80/443 open | — | pending charges $1.65 of $200.00 |
| 2026-11-04 12:10 | Updated | VM cloud-init `user_data` to the current template (in place, no reboot: uptime unchanged; cloud-init does not rerun, so it only affects a rebuild) | — | — |
| 2026-11-04 12:15 | Opened, closed | SSH for 76.19.41.155/32 with `access.sh` to copy the demo logins file to `install-smb-demo/` (gitignored); `terraform plan` now shows no changes; only 80/443 open | — | pending charges $1.96 of $200.00 |
| 2026-11-04 23:28 | Opened, closed | SSH for 76.19.41.155/32 (about 10 minutes) for the P6-08 restore drill and the P6-06 egress test; one manual backup (≈ 0.9 MB) to Object Storage; `terraform plan` no changes afterwards; only 80/443 open | — | pending charges $2.65 of $200.00 |
| 2026-11-05 01:40 | Upgraded | VM to 0.1.5 (images pushed, registry ≈ 4 GB of the free 10 GB; SSH opened about 6 minutes for the upgrade and a restore drill, then closed; `terraform plan` no changes) | — | — |
| 2026-11-06 13:20 | Upgraded | VM to 0.1.6 (images pushed, registry ≈ 4.7 GB of the free 10 GB; SSH opened about 10 minutes for the upgrade, the route-file swap, demo knowledge articles and a live front-desk check, then closed; `terraform plan` no changes) | — | pending charges $5.65 of $200.00 |
| 2026-11-06 19:05 | Upgraded | VM to 0.1.7 (images pushed, registry ≈ 5.4 GB of the free 10 GB; SSH opened about 10 minutes for the upgrade, compose.yml copy (comments only), route-file swap and an in-container live check, then closed; `terraform plan` no changes) | — | pending charges $6.04 of $200.00 |
| 2026-11-06 22:40 | Upgraded | VM to 0.1.8 (images pushed; SSH opened about 8 minutes for the upgrade and an in-container live check, then closed; deployment and route files unchanged; `terraform plan` no changes) | — | pending charges $6.70 of $200.00 |
| 2026-11-07 01:10 | Upgraded | VM to 0.1.9 (images pushed; SSH opened about 12 minutes for the upgrade, route-file swap (new `embed` route), dental demo seed, knowledge indexing and live checks, then closed; `terraform plan` no changes) | — | pending charges $7.40 of $200.00 |
| 2026-11-07 03:15 | Upgraded | VM to 0.1.10 (images pushed; SSH opened about 6 minutes for the upgrade, then closed; deployment and route files unchanged; `terraform plan` no changes) | — | pending charges $7.52 of $200.00 |
| 2026-11-07 05:15 | Upgraded | VM to 0.1.11 (images pushed; SSH opened about 6 minutes for the upgrade, route-file swap with Vultr catalog prices and a live cost check, then closed; `terraform plan` no changes) | — | pending charges $7.64 of $200.00 |
| 2026-11-07 12:55 | Changed | Demo domain `45-63-85-83.sslip.io` → `tandemai.open-dl.com` (Cloudflare A record, DNS only, owner's zone; SSH opened about 10 minutes, manual backup, Keycloak client and `.env` updated, Let's Encrypt certificate, smoke passed, SSH closed; `terraform plan` no changes) | — | pending charges $7.64 of $200.00 (no new resources) |

## Running resources (2026-11-04 12:15 UTC)

| Resource | Cost | Keep? |
|---|---|---|
| VM `smb-demo` (vhp-2c-4gb-amd) | $24/month | Owner decides: it is the live demo. Destroy with `docs/runbooks/teardown.md` when not needed |
| Object Storage `smb-demo-backups` (Standard) | $18/month | Needed while the VM's backups matter |
| Container Registry `smbopsrqk49` | free | Yes |
| Serverless Inference | per token (≈ $1.5 so far, mostly evals) | Yes; budget ceilings in the app (`AI_DAILY_COST_LIMIT_USD`, `AI_MONTHLY_COST_LIMIT_USD`) |

At $42/month for the VM and storage, the remaining credit lasts about 4.5 months with today's eval-level AI use.
Terraform state matches the VM (no pending changes, 2026-11-04 12:15). This VM predates the host-firewall hardening:
its UFW still allows 22/tcp broadly, so the Vultr firewall group is the only SSH gate (see `docs/runbooks/access.md`).
