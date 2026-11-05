# Monitoring and host housekeeping

The VM checks itself every five minutes using `smb-watchdog.timer`. This is a host script, with no additional container or monitoring subscription. **A dead VM cannot alert about itself.** Before handover, the owner must configure an independently hosted uptime check for `https://<domain>/api/v1/readyz` (HTTP 200 and `status: ok`), TLS expiry and preferably login discovery. Deliver those alerts through an independent operator channel. Record the provider/check URL and a successful forced-outage notification in the handover record.

Name the primary operator, backup operator, alert destination and escalation contact in the handover record before relying on unattended operations. Empty fields or an untested webhook are incomplete handover requirements.

## Installation and existing VMs

New VMs get systemd units, journal limits, key-only SSH, restricted host SSH sources and verified NTP from cloud-init. `install.sh` stages the watchdog and verifies the active timer and synchronized clock. Ubuntu's existing Python standard library is used; no pip installation is needed.

For an **existing** installation, stage and copy `watchdog.sh`, `watchdog.py`, `install-host-ops.sh` and the updated `smoke.sh` into `/opt/smb` with the release deployment files. Set shell scripts executable. Then, in the Vultr **root console**, run:

```bash
/opt/smb/install-host-ops.sh
systemctl status smb-watchdog.timer
systemctl list-timers smb-watchdog.timer
timedatectl show --property=NTPSynchronized --value
```

The last command must say `yes`; the setup script fails if synchronization does not arrive in 60 seconds. Investigate the configured NTP service, outbound NTP and `journalctl -u systemd-timesyncd` on failure. The one-off script does not change SSH/UFW: review the explicit existing-VM steps in [access.md](access.md). `upgrade.sh` alone does not install host units. The next timer run is within five minutes; for immediate validation run `systemctl start smb-watchdog.service`.

## Alert destination and receipt test

`gen-env.sh` now includes an empty `ALERT_WEBHOOK_URL=`. Add that entry to an existing installation's `/opt/smb/.env` and protected offline copy yourself; keep both mode 600. Set a destination that accepts an HTTP JSON POST, such as a Slack-compatible webhook, an ntfy topic URL or a mail bridge. Use HTTPS for remote destinations; HTTP is supported for a trusted local bridge. No alert URL is passed into application containers. The watcher reads the setting each time; restarting containers is unnecessary. It reads configuration as data and never executes shell substitutions.

The payload contains `text`, `status` (`alert`, `ok`, or `test`) and an `issues` array. The alert text lists check names and failure conditions, without customer data, command output or credentials. ntfy topic endpoints may display the JSON body verbatim; use a bridge if you need custom formatting or authentication headers. Webhook redirects are refused, and delivery has a 10-second timeout. With an empty URL, changes only go to the local journal/status file.

```bash
# As root, from the console (uses the configured destination):
/opt/smb/watchdog.sh --test
journalctl -u smb-watchdog.service --since '30 minutes ago' --no-pager
cat /var/lib/smb-watchdog/status.json
```

`--test` sends exactly one test message and does not run probes or change operational state. Confirm **actual receipt** with the primary and backup operators; a successful HTTP response alone does not prove a human received it. Never paste a webhook URL into logs or a ticket. With no URL set the test only logs, which does not verify alert delivery.

Every regular run atomically replaces `status.json` (root-only directory/file) with `checked_at`, issues, observations and delivery status. An initial state, any changed set of issues, and recovery produce a message; unchanged conditions do not repeat. A failed POST is recorded as `delivery: failed` and retried at the next run. Check failures are reported as unavailable, never silently interpreted as healthy. A process/configuration error fails the systemd service and logs locally; the external uptime check remains necessary. Check timer/service failures during the operator's daily review.

## Checks and thresholds

| Check | Alert condition |
|---|---|
| HTTPS readiness from the VM | TLS/HTTP failure, invalid response or `status` other than `ok` at `/api/v1/readyz` |
| Every Compose service | Missing or not running/healthy; the one-shot `migrate` must exist and have exited successfully |
| Restarts and OOM | Restart count increases since last sample (first sample compares to zero); Docker OOM flag or OOM kill in the last 10 minutes of kernel journal, falling back to `dmesg` |
| Host storage | Less than 5 GiB available on `/opt/smb` or `/var/lib/docker`; fewer than 10,000 free inodes or less than 5% free |
| Backup | Latest attempt not `ok`, missing success or success older than **7 hours** (future timestamps also fail) |
| Maintenance | Independent prune/check result not `ok`, missing success or success older than **8 days** |
| Jobs | Any `dead` job, queued/failed job due more than 15 minutes ago, or running job whose lease expired more than 5 minutes ago (including missing lease) |
| TLS certificate | Public certificate expires in less than **14 days**, or retrieval/validation fails |
| Time | `timedatectl` does not report `NTPSynchronized=yes`, or the check is unavailable |

Job checks execute only aggregate read-only SQL through the existing PostgreSQL container with a five-second statement timeout; no job payloads or customer identifiers are returned. Missing database/schema telemetry is an alert. Future scheduled work is excluded by its `run_at`. Dead jobs keep an alert active until investigated; follow the job incident procedure rather than deleting evidence to silence alerts. Restart warnings clear on the next stable sample; kernel OOM warnings clear when the ten-minute window passes. `NTPSynchronized` reports systemd's synchronization state, not a measured clock-offset bound.

After the new backup image starts, run `docker compose exec backup backup.sh --maintenance` in a quiet window to seed prune/check status. Check `backup.sh --status` afterward. A failed maintenance operation preserves its previous success timestamp and does not replace the backup result. Existing retention and random 5% weekly verification remain unchanged; this batch does not prove whole-repository integrity coverage.

## Journal retention and disk cleanup

The host journal is limited to 256 MiB persistent / 64 MiB runtime, with 1 GiB kept free and a 14-day maximum age. `install-host-ops.sh` applies this to existing hosts and restarts journald. During an existing disk incident, root may run `journalctl --vacuum-size=256M --vacuum-time=14d` after preserving necessary incident evidence; the configured limits alone do not immediately remove all existing journals. Docker's existing 10 MiB × 5 per-container log rotation stays enabled.

Once a month, and after a successful upgrade/restore plus a tested rollback, review disk consumption. **Never use global `docker system prune`, `docker image prune -a`, or volume pruning on a shared host.** For images, list only the installation's exact registry repositories (`<registry>` is the configured image prefix):

```bash
docker image ls '<registry>/smb-api:*'
docker image ls '<registry>/smb-web:*'
docker image ls '<registry>/smb-backup:*'
# After review, delete explicitly named old tags only; never force removal:
docker image rm <registry>/smb-api:<obsolete-version> <registry>/smb-web:<obsolete-version> <registry>/smb-backup:<obsolete-version>
```

Keep the **current and immediately previous application releases**, any separately pinned `MIGRATION_VERSION` API image, and all tags used by running containers. Keep the preceding deployment files too. Do not remove third-party images or dangling image IDs without establishing this project's sole ownership. Not using `--force` preserves images referenced by containers and avoids untagging another project's shared reference inadvertently. Cleanup is deliberately operator reviewed, not part of the unattended watchdog.

Restore scratch databases can be dropped **after** recording a successful drill and checking that no one still uses them. Connect only to this installation's `smb-prod` Compose project in `/opt/smb`:

```bash
cd /opt/smb
docker compose exec postgres psql -X -U postgres -d postgres
```

Inspect `pg_database` and `pg_stat_activity` first. Only the exact scratch names `smb_drill`, `keycloak_drill`, `smb_restore`, `keycloak_restore` and strictly timestamped `smb_pre_restore_YYYYMMDDHHMMSS` / `keycloak_pre_restore_YYYYMMDDHHMMSS` belong to this procedure. Preserve the newest pre-restore database for **each** live database as a recovery fallback. This read-only query proposes deletion commands for older pre-restore copies at least 14 days old, excluding any database with connected sessions:

```sql
WITH copies AS (
  SELECT datname, split_part(datname, '_pre_restore_', 1) AS source,
         substring(datname from '[0-9]{14}$') AS stamp
  FROM pg_database
  WHERE datname ~ '^(smb|keycloak)_pre_restore_[0-9]{14}$'
), ranked AS (
  SELECT *, row_number() OVER (PARTITION BY source ORDER BY stamp DESC) AS n
  FROM copies
)
SELECT format('DROP DATABASE %I;', datname)
FROM ranked
WHERE n > 1 AND stamp < to_char(now() AT TIME ZONE 'UTC' - interval '14 days', 'YYYYMMDDHH24MISS')
  AND NOT EXISTS (SELECT 1 FROM pg_stat_activity a WHERE a.datname = ranked.datname);
```

Review and execute selected statements individually in the same session; no `FORCE`, no automatic `\gexec`. A new connection can make a drop fail safely. Exact scratch names have no creation timestamp: drop them manually only after the corresponding drill/restore is accepted. Never drop live `smb`/`keycloak`, system databases, another project's databases, or the newest fallback. Confirm a fresh off-VM backup before deleting any recovery copy.

## Acceptance record

Record timer enabled/active, synchronized clock, fresh local status, backup/prune/check timestamps, actual test-alert receipt, and independent external-check receipt. During a scheduled rehearsal, inject each condition using synthetic/stubbed probes or a controlled disposable deployment and record alert and recovery receipt. The repository tests exercise probe results, state changes and transports with stubs; they do not verify live VM configuration, shutdown under load, certificate renewal or external delivery.
