# Batch F work record

Date: 2026-11-04 UTC (2026-11-03 America/New_York), after kickoff. Kind: PRE deployment/operations work; no inference, agent or application changes. This scoped record avoids editing the shared root work log during concurrent batches.

Implemented review findings #1, #10, #18, #20, host #9, and deployment portions of #7/#11. No commits, cloud operations, real Docker calls, network requests, real environment-file reads, dependencies, containers or application services were added/run during this work. The watchdog is an explicitly requested host systemd service/timer and uses existing Ubuntu tools. All environment files used by tests are synthetic temporary fixtures.

## Changed files (this batch only)

- `CHANGELOG.md`: additive operator bullets under 0.1.1 only.
- `deploy/compose/compose.prod.yml`, `env.prod.template`.
- `deploy/scripts/backup.sh`, `check-deploy.sh`, `gen-env.sh`, `install.sh`, `smoke.sh`, `stage.sh`.
- New `deploy/scripts/watchdog.sh`, `watchdog.py`, `install-host-ops.sh`.
- `deploy/terraform/modules/vultr-single-vm/main.tf`, `user-data.yaml.tftpl`.
- New `deploy/tests/test_watchdog.py`, `test_backup_monitoring.py`, `test_ops_config.py`.
- `docs/runbooks/README.md`, `access.md`, `backup-restore.md`, `upgrade.md`; new `monitoring.md`.
- `docs/roadmap/10-vultr-infrastructure.md` and this new work record.

Existing changes in this checkout, including changes already present in some listed files, were preserved. No backend files were edited.

## Requirement evidence and limits

| Requirement | Evidence | Required-condition limit |
|---|---|---|
| #1 host monitoring, five-minute timer, status changes, JSON webhook, logging only, test alert | Watchdog scripts; matching cloud-init and retrofit units; 12 stubbed watchdog tests | No VM run or real webhook receipt; external uptime remains owner configured |
| HTTPS readiness; containers/health/restarts/OOM; bytes/inodes; backups/jobs; TLS <14 days; clock state | Stubbed healthy/unhealthy/unavailable probes with bounded read-only SQL and timestamps | No production Docker, SQL, HTTPS, certificate or kernel journal probe executed |
| #10 seven-hour backup health and separate maintenance success/failure | Three script regressions execute backup health/status/maintenance with restic/flock/date stubs; watchdog checks status independently | No repository maintenance or integrity verification run |
| #18 key-only SSH, root key policy, UFW/provider CIDRs | Terraform passes same CIDRs; YAML policy tests for empty, IPv4 and IPv6 inputs; access runbook has existing-VM/changed-CIDR steps | No live SSH authentication or IPv4/IPv6 firewall verification |
| #20 enable/verify NTP, smoke/watchdog checks | Cloud-init/retrofit enable NTP and fail verification on timeout; installer verifies clock and timer; Linux smoke checks clock; stubbed watchdog clock results | No live synchronization or clock-offset measurement |
| #9 journal bounds; project-only old image/restore DB cleanup; retain rollback release | Matching journal drop-ins; reviewed cleanup commands/query in monitoring runbook, preserving previous release, migration pin and newest per-DB fallback | No host journal vacuum, image removal or database deletion executed |
| #7/#11 API/worker stop grace ≥100 seconds; idle transaction timeout; upgrade window | Compose YAML test requires ≥100 seconds (configured 120); PostgreSQL command sets 60-second idle transaction limit; upgrade runbook requires drain/window | No shutdown-under-load, migration/restart or rollback rehearsal on Docker |
| Existing install gets watchdog | Staging regression includes scripts; explicit root-console one-off in CHANGELOG and runbooks | `upgrade.sh` alone intentionally does not install systemd units; one-off not run on VM |
| Cloud-init/Compose configuration | Offline YAML regressions; native Terraform `templatefile` + `yamldecode` for three CIDR variants; Terraform format check | Full cloud-init schema and Docker Compose resolution unavailable under no-Docker/no-network constraints |

## Operator changes

1. Stage updated deployment files; rebuild/publish backup image through the normal authorized release process. No batch F database migration.
2. Existing VMs: run staged `/opt/smb/install-host-ops.sh` once as root in the console; verify timer, status and NTP. New VMs receive units from cloud-init automatically. Calendar scheduling repeats even if first run preceded script upload.
3. Configure optional `ALERT_WEBHOOK_URL` in both protected environment copies, test actual alert receipt, assign primary/backup operators and configure independent external uptime monitoring. Empty URL means local logs only.
4. Initialize independent maintenance status with one quiet-window `backup.sh --maintenance`. Seven-hour backup alert/health threshold; failures/absence of maintenance status and success older than eight days alert.
5. Reserve a maintenance window, drain writers, allow 120-second API/worker shutdown, and recreate PostgreSQL for its 60-second idle transaction limit. Follow the release deployment-file procedure.
6. New VMs explicitly restrict SSH authentication and host sources. Existing VMs require console review/application; keep UFW and provider CIDRs aligned for later opens/closes, including installer `--close-ssh`.
7. Journal caps are 256 MiB persistent, 64 MiB runtime, 1 GiB free reserve, 14 days; old journals may need a reviewed vacuum. Monthly image/database cleanup is manual and scoped; preserve rollback release/configuration, migration pin, latest pre-restore fallback and unrelated projects.

## Verification

Final `DEPLOY_CHECK_OFFLINE=1 DEPLOY_CHECK_NO_DOWNLOADS=1 make deploy-check`:

```text
ok    shellcheck: all scripts
ok    deployment regressions (live Caddy tests self-skip if unavailable)
Ran 98 tests in 73.957s
OK (skipped=19)
skip  Docker Compose resolution, Caddy runtime, Terraform provider and cloud-init schema
ok    install.sh --dry-run prints every step and creates nothing (8 steps)
deploy checks: ALL PASSED
```

That is **79 passed, 19 skipped**; all **20 new batch F tests** passed (12 watchdog, 3 backup health/maintenance, 5 host configuration/staging). Docker/Caddy tools were excluded from this explicit offline run so tests could not start containers/listeners. Normal deploy-check behavior is unchanged outside offline mode.

Additional checks: `shellcheck deploy/scripts/*.sh deploy/scripts/lib/*.sh` passed; `terraform -chdir=deploy/terraform fmt -check -recursive` passed; native ARM Terraform `templatefile` + `yamldecode` passed all three CIDR variants and confirmed the recurring timer; scoped Ruff lint and format passed for all four new Python files; scoped `git diff --check` passed. Real cloud-init schema validation and Docker Compose resolution were not run. No production/live claims follow from these static/stubbed results.

Full `make check` and backend pytest were not run: this batch changes no backend modules, and the user's scoped commands/no-Docker/no-network constraints supersede the repository-wide suite.

Final backend command `.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy app` passed: **All checks passed!; 484 files already formatted; Success: no issues found in 278 source files**. Earlier shared-checkout lint findings in another batch were resolved by concurrent work; no backend files were changed by batch F. These results describe the shared checkout at check time.
