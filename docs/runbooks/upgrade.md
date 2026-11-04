# Runbook: upgrade and roll back

Time: 5–10 minutes. Brief downtime (seconds to a minute) while containers restart. Read [README.md](README.md) first for the placeholders and conventions.

## What an upgrade changes

`upgrade.sh` changes the **application image version** (api, worker, web/Caddy, backup) and runs the application's database migration. It does **not** copy new deployment files to the VM. Third-party images (PostgreSQL, Keycloak) are pinned in `/opt/smb/compose.yml` and only change when a release says so (see "When a release changes the deployment files").

Database changes are **expand/contract**: a release adds things the previous release ignores (the "expand" step). Removing old structures (the "contract" step) happens only in a later release whose notes say that you cannot go back past it. So rolling back one release is safe for the application. Rolling back never undoes database changes and never touches Keycloak's own database.

## Before

1. Decide the target version `<new-version>` (for example `0.1.1`). It is **not** necessarily the next number: check what runs now with `vm$ cat /opt/smb/VERSION`. Do not go to a lower number than the current one (use the restore runbook if you must go back).
2. Read the release notes for `<new-version>`: the project's `CHANGELOG.md` (until one exists, ask the developers; do not upgrade without notes). Look for "migration" and "manual step". Manual steps marked "before" are done before you start; those marked "after" after step 7 below.
3. The three images (`smb-api`, `smb-web`, `smb-backup`) of `<new-version>` must exist in the registry ([install.md](install.md) section 2).
4. Choose a quiet time and tell the owner.
5. Check the VM can still pull from the registry (Vultr registry credentials have a validity period): `vm$ cd /opt/smb && docker compose pull` re-pulls the current version. If it says "unauthorized", create a new read-only credential and `docker login` again ([rotate-secrets.md](rotate-secrets.md)).

## Upgrade

If your SSH connection may drop (slow Wi-Fi), start a persistent session first (`vm$ tmux`; reconnect with `tmux attach`). If the script is interrupted anyway (connection lost, Ctrl-C), it rolls back to the old version by itself, which you confirm with the smoke test.

```bash
$ ssh -i install-<name>/id_ed25519 smb@<ip>
vm$ cd /opt/smb
vm$ ./upgrade.sh <new-version>
```

`upgrade.sh` does, in order, and stops at the first problem:

1. Takes a backup now (tagged `pre-upgrade`) and waits for it. If that backup fails, nothing else happens.
2. Writes the new `VERSION` into `.env` (and `/opt/smb/VERSION`).
3. `docker compose pull` (never set `SKIP_PULL=1` on a VM; that variable exists only for local rehearsals with locally built images).
4. Runs the database migration (`docker compose run --rm migrate`).
5. Hands the volumes of the web container (Caddy) to its unprivileged user. Nothing happens when they already belong to it; see "The release in which the web container stops running as root" below.
6. `docker compose up -d`, waits up to 5 minutes for every service to be healthy.
7. Runs `smoke.sh`.

**Outcomes you can see at the end of the output:**
- `upgraded to <new-version>`: success.
- `ROLLING BACK to <old-version>` followed by no further warning: the upgrade failed, the previous version was started again and is healthy. Confirm with `vm$ ./smoke.sh https://<domain> --compose-dir /opt/smb` (all `PASS`).
- `ROLLING BACK ...` followed by `rollback is also not healthy`: both versions fail. Go to [incident.md](incident.md).

## After

```bash
vm$ tail -3 /opt/smb/upgrade.log      # time | from -> to | OK, or FAILED, rolled back to <old-version>
vm$ cat /opt/smb/VERSION              # mirrors VERSION in .env, which is what Compose uses
vm$ docker compose ps --format '{{.Service}} {{.Image}} {{.Status}}'   # image tag and state of every running container
```

If `VERSION`, `.env` and the running images disagree, trust the running images (they are what serves users) and set `VERSION=` in `.env` and the content of `/opt/smb/VERSION` to match before the next upgrade; if different services run different versions, run `docker compose up -d` once more and check again.

## When a release changes the deployment files

Release notes say so explicitly ("compose.yml changed", "scripts changed"). Do this **instead of** going straight to `upgrade.sh`:

```bash
vm$ cd /opt/smb
vm$ docker compose exec backup backup.sh --kind manual             # backup under the CURRENT configuration first
$ deploy/scripts/stage.sh /tmp/stage-<new-version>                  # on your computer, with the new version checked out
$ scp -i install-<name>/id_ed25519 /tmp/stage-<new-version>/compose.yml smb@<ip>:/opt/smb/compose.yml.new
vm$ cp compose.yml compose.yml.previous && mv compose.yml.new compose.yml
vm$ ./upgrade.sh <new-version>
```

The notes also list any other changed file (`smoke.sh`, `upgrade.sh`, `restore.sh`, `postgres-init.sh`, the realm file): copy those from `/tmp/stage-<new-version>/` the same way before the upgrade.

If the upgrade fails, the automatic rollback starts the **old images under the new `compose.yml`**. Put the old file back and re-apply it:

```bash
vm$ mv compose.yml.previous compose.yml
vm$ docker compose up -d --remove-orphans
vm$ ./smoke.sh https://<domain> --compose-dir /opt/smb     # wait a minute first; all PASS
```

(`--remove-orphans` removes containers of services that only the new file had.) A change of a pinned third-party image (PostgreSQL, Keycloak) is only ever made together with release notes that say whether going back is possible; if they say it is not, restore from the backup instead ([backup-restore.md](backup-restore.md)).

## The release in which the web container stops running as root

Up to `0.1.0-rc3` the web container (Caddy, the only process reachable from the Internet) ran as root with Docker's default capabilities. From the release that follows it runs as the fixed user `10003`, with every capability dropped except `NET_BIND_SERVICE`, `no-new-privileges` and a read-only root filesystem; PostgreSQL is started as its own user `999` instead of root, and Keycloak (already user `1000`) drops its capabilities too. That release changes `compose.yml`, `upgrade.sh` and `smoke.sh`, so use the procedure above ("When a release changes the deployment files") and copy **all three**.

**Why there is one extra step.** The Docker volumes `caddy_data` (certificates and the Let's Encrypt account) and `caddy_config` of an installation made earlier belong to root. The new container starts and looks healthy, but it cannot read its certificate, so HTTPS does not work (its log says `permission denied`). The new `upgrade.sh` therefore hands both volumes to user `10003` itself, after the database migration and before it starts the new version. It is quick and can be repeated, and it keeps the existing certificate: no new certificate is ordered, which matters because Let's Encrypt limits how often one can be.

If you do **not** use the new `upgrade.sh` (an older script, or you only change `compose.yml` and run `docker compose up -d`), run this once **before** the new containers start. It uses the `caddy` service of the compose file you have, creates a throwaway container and gives it only the two capabilities `chown` needs:

```bash
vm$ cd /opt/smb
vm$ docker compose run --rm --no-deps -T --user 0:0 --cap-add CHOWN --cap-add DAC_READ_SEARCH --entrypoint chown caddy -R -h 10003:10003 /data /config
vm$ docker compose run --rm --no-deps -T --entrypoint find caddy /data /config ! -user 10003    # prints nothing when it is done
```

Afterwards `./smoke.sh https://<domain> --compose-dir /opt/smb` shows these lines as `PASS` (all of them are new in this release):

```text
no service runs as root
caddy keeps one capability, NET_BIND_SERVICE
caddy cannot gain privileges (no-new-privileges)
caddy root filesystem is read-only
all files in caddy's volumes belong to its user (uid 10003)
no Server header on the app / on the api / on a missing asset
plain HTTP redirects to HTTPS (308) / the HTTP redirect has no Server header
API description is not public (/api/v1/openapi.json is 404) / API docs are not public (/api/v1/docs is 404)
```

The two `API ...` checks fail when the api does not run in production mode (it then publishes its API description): check `vm$ grep '^APP_ENV=' /opt/smb/.env`, which must say `prod` on a real installation (only a local rehearsal sets `dev`, while its end-to-end tests run).

If HTTPS fails after an upgrade although every container is healthy, check `vm$ docker compose logs caddy | grep -c 'permission denied'`. A number above zero means the volumes were not handed over: run the `chown` command above and `vm$ docker compose restart caddy`.

**Going back needs nothing extra.** The previous images start under the new `compose.yml` (checked with `0.1.0-rc3`; this is what the automatic rollback does), and the old `compose.yml` runs fine on volumes that belong to `10003`, because root may read them. The volumes do not have to be handed back.

## If the rollback is not enough

After a migration succeeds, rollback keeps `VERSION` at the previous app release and persists
`MIGRATION_VERSION` at the release that knows the database revision. Keep that API image available;
normal Compose down/up and `smb.service` restarts use it for migrations while starting the old app.
Do not reset this pin to an older release or downgrade the database. A successful later upgrade
sets both versions to its release. Deployment files must include this migration-image setting;
stage the updated `compose.yml` before using the updated upgrade script.

Signs: the previous version is also unhealthy, or data looks wrong. Do not repeat the upgrade. Follow [incident.md](incident.md) and, if data is damaged, [backup-restore.md](backup-restore.md). The snapshot taken at step 1 is the **most recent** one in `vm$ docker compose exec backup restic snapshots --tag pre-upgrade` (its time matches the first line of this upgrade's output): use its **id** in the restore, not `latest`.

## Several versions behind

Upgrade one release at a time, in order (`0.1.1`, then `0.1.2`), unless the release notes of the later version say it can be skipped to. The ordered list of releases is the project's `CHANGELOG.md`.
