# Runbook: backup, restore drill, real restore

Read [README.md](README.md) first (placeholders, working directory). On the VM, every command here starts from `vm$ cd /opt/smb`.

## How backups work

The `backup` container runs `pg_dump` of the `smb` (business data) and `keycloak` (logins) databases at start-up (if the last backup is older than 6 hours), every 6 hours, and nightly at 02:30 UTC. Dumps go through **restic** (encrypted with the restic password, deduplicated) into the repository in `.env` (`RESTIC_REPOSITORY`, a Vultr Object Storage bucket in a real install). Weekly it applies retention (7 daily, 4 weekly, 6 monthly) and checks 5 % of the stored data. With working backups, at most about 6 hours of data can be lost.

You need the **restic password** (stored offline at install time), the bucket keys, and the installation's `.env` to restore on a different machine. Keep an **encrypted** copy of `install-<name>/.env` away from the computer you installed from (password manager attachment or an encrypted drive): if that computer is lost, so are the database passwords the restored data needs.

## Check that backups are healthy

```bash
vm$ cd /opt/smb
vm$ docker compose exec backup backup.sh --status
```

The container is healthy only when the latest backup attempt succeeded and its last success is at most **7 hours** old (the schedule is every **6 hours**). A failed attempt or an older backup is urgent: read `docker compose logs --tail 50 backup`.

`--status` also includes independent `maintenance.prune` and `maintenance.check` objects with `result`, `last_attempt`, and `last_success`. A failed prune/check preserves its previous success timestamp and stays visible until that operation succeeds; it never replaces backup status. The [watchdog](monitoring.md) alerts on failure, missing status, or maintenance success older than 8 days. After updating the backup image, run `docker compose exec backup backup.sh --maintenance` once during a quiet window to initialize these fields. Weekly scheduling is unchanged; a random 5% check does not prove complete repository coverage. List snapshots (newest last):

```bash
vm$ docker compose exec backup restic snapshots
```

## Manual backup

```bash
vm$ docker compose exec backup backup.sh --kind manual
vm$ docker compose exec backup backup.sh --status      # must say "result": "ok" and a last_success of the last minute
```

## Restore drill (nothing live is changed)

Do this after install, after upgrades that include a migration, and at least quarterly. Check there is room first: `vm$ df -h /` and `vm$ docker compose exec postgres psql -U postgres -c "select pg_size_pretty(pg_database_size('smb')), pg_size_pretty(pg_database_size('keycloak'))"`; you need free space of at least the two sizes added together (the drill makes a copy of both). A real restore needs twice that (it keeps the old databases next to the restored ones).

Take a backup right before, so the snapshot is as close to live as possible and the comparison is meaningful:

```bash
vm$ docker compose exec backup backup.sh --kind manual
vm$ ./restore.sh --drill latest
```

It restores the newest snapshot into scratch databases named `smb_drill` and `keycloak_drill` (it **replaces** any database with those names: do not use them for anything else; the live databases are never touched), prints the migration revision and the number of tables and rows, and compares every table's row count with the live database.

Read details with `vm$ docker compose exec backup cat /state/restore-last.json`: a JSON array covering both databases of the last drill or real restore. Each entry includes `database`, `snapshot`, `target`, `tables`, `rows`, `counts_identical_to_source` (`true`/`false`, or `null` when no comparison was requested), `swapped`, `restore_seconds`, `total_seconds`, and `at`. Differing counts still produce an entry with `false`; per-table differences are printed above on stderr during the drill. Each run replaces the previous details; a failed run can contain only the databases that reached comparison.

**Pass criteria:** the command finishes without "DRILL FAILED"; the output shows the restored tables, rows and migration; and the only tables whose counts differ from live are busy technical ones (`jobs`, `oidc_login_states`, `sessions`, `rate_limits`, `domain_events`, `event_deliveries`, `audit_events` on a busy system). A difference in a business table (for example `customers`, `appointments`, `stock_movements`) bigger than the few records people created in the minute since the manual backup, or any "DRILL FAILED" line, is a failure: repeat; if it persists, escalate with the output of the drill and `backup.sh --status` ([README.md](README.md), "Who to call"). A passing drill proves the **new** snapshot works; older snapshots are only as good as the backup that wrote them. Equal row counts show nothing is missing wholesale; they do not prove every value is right.

Record the duration (the line "drill finished in N s") in your log, then remove the scratch databases:

```bash
vm$ docker compose exec postgres psql -U postgres -c 'drop database smb_drill' -c 'drop database keycloak_drill'
```

## Real restore (replaces the live data)

Use when the database is lost or corrupted. **Everything written after the snapshot is lost.**

1. Choose the snapshot by **date**, not by habit:

   ```bash
   vm$ docker compose exec backup restic snapshots
   ```

   Pick the newest snapshot taken **before** the problem started (the table shows time and tags: `scheduled`, `nightly`, `pre-upgrade`, `manual`, ...). If you cannot tell when it started, take an older one and accept more data loss. Each snapshot contains both databases (dumped one after the other a few seconds apart; they hold independent data, so a login created in that gap could at worst be missing from one of them).
2. Run it with the snapshot id (for example `a1b2c3d4`):

   ```bash
   vm$ ./restore.sh a1b2c3d4 --yes
   ```

   (`./restore.sh latest --yes` is allowed: the script first resolves `latest` to an id, then takes its safety backup, so it restores the previous newest snapshot, not the safety backup.)

   What it does: takes a safety backup of the current state (tagged `pre-restore`; waits for the backup lock; if the database is too damaged for that, it prints a WARNING and continues, because you asked for a restore); stops caddy, api, worker and keycloak (so nobody writes during the swap); restores both databases into new databases; renames existing old databases to `smb_pre_restore_<time>` and `keycloak_pre_restore_<time>` (a missing original does not prevent promotion); checks that both restored Keycloak client secrets match the current `.env`; applies database migrations using the configured migration image (an older snapshot may predate them); starts everything; runs the smoke test (waits up to 5 minutes). Writes made between the safety backup and the shutdown a few seconds later are in the old (renamed) databases only.

   **If the client-secret check fails**, the restore exits unsuccessfully and leaves the application stopped. Realm import cannot update an existing restored realm. Start only Keycloak with `docker compose up -d --no-deps keycloak`, open its admin console through the SSH tunnel ([access.md](access.md)), and reconcile **both** `smb-app` and `smb-admin` credentials with the current protected `.env` using [rotate-secrets.md](rotate-secrets.md). The admin login is the one from the snapshot; the bootstrap password does not reset it. If necessary, rotate the clients and update both `.env` copies. Then run `docker compose run --rm -T --no-deps migrate`, `docker compose up -d`, and `./smoke.sh https://<domain> --compose-dir /opt/smb`. Verify a fresh sign-in and staff provisioning before recording recovery as complete. Do not repeat the database restore just to retry these follow-up steps.
3. If it ends with "restore complete; smoke test passed", check by hand: `./smoke.sh https://<domain> --compose-dir /opt/smb`, sign in as the owner, open the business's Customers and Schedule pages, and confirm the most recent records you expect from before the snapshot are there.
4. If it fails part-way, read its last lines and list the databases: `vm$ docker compose exec postgres psql -U postgres -c '\l'`. Then:
   - **No `smb_pre_restore_*` / `keycloak_pre_restore_*` in the list**: it failed before the swap and your live databases are untouched. Drop the half-restored copies (`drop database smb_restore`, `drop database keycloak_restore`, whichever exist), run `docker compose up -d`, smoke test. Try again or escalate.
   - **`smb_pre_restore_<time>` exists** (the swap happened) and you want to go back to the old data: with the stack stopped (`docker compose stop caddy api worker keycloak`), open `docker compose exec postgres psql -U postgres -d postgres` and run (replace `<time>`; do the same pair for `keycloak` if its swap happened too): `alter database smb rename to smb_failed_restore;` then `alter database smb_pre_restore_<time> rename to smb;`. If a rename says the database is in use, run `select pg_terminate_backend(pid) from pg_stat_activity where datname = 'smb' and pid <> pg_backend_pid();` and repeat. Then `docker compose up -d` and run the smoke test. Note the old data may contain the very corruption you were fleeing.
   - If you are not sure which case you are in, stop and escalate.
5. Keep the old databases until you are sure. List them with `docker compose exec postgres psql -U postgres -c '\l'`, then drop **both** (replace the name with exactly what the list shows):

   ```bash
   vm$ docker compose exec postgres psql -U postgres -c 'drop database smb_pre_restore_20261026123000' -c 'drop database keycloak_pre_restore_20261026123000'
   ```

### Restoring on a new VM (the old one is gone)

1. Do [install.md](install.md) with the **same** installation `.env` secrets (put your saved `.env` in `install-<name>/` before running the installer with `--resume`), the same restic password, bucket and keys, and **stop before step 6 (bootstrap)**: do not create a business.
2. **Warning:** the new installation's backup container takes a first backup at start-up, which stores the **empty new databases** as the newest snapshot. Do **not** use `latest`. Run `docker compose exec backup restic snapshots`, and pick the newest snapshot from the old installation (by date, before the loss).
3. Run `vm$ ./restore.sh <that-snapshot-id> --yes`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| status shows `"result": "error"` | `docker compose logs --tail 50 backup`. Usual causes: wrong bucket keys, bucket missing, restic password changed, Postgres down. |
| "wrong password or no key found" | The restic password in `.env` differs from the one the repository was created with. Use the offline copy. |
| "DRILL FAILED: restoring smb exited with status ..." | The snapshot is unreadable, the repository is unreachable, or the restore itself failed: read the lines above it. Do not trust that backup until a manual backup and a new drill pass. |
| Drill reports differing counts only in busy technical tables | Expected (see pass criteria). |
