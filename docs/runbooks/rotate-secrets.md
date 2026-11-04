# Runbook: rotate secrets

Read [README.md](README.md) first. On the VM, start with `vm$ cd /opt/smb`.

## Where each secret lives

| Secret | Lives in | Notes |
|---|---|---|
| Database passwords, `APP_SECRET_KEY`, Keycloak secrets, restic password, Object Storage keys, inference keys | `/opt/smb/.env` on the VM **and** your saved copy `install-<name>/.env` | Keep both equal. Both are mode 600. |
| Registry read-only credential | Docker's login file of the `smb` user on the VM (`~/.docker/config.json`) | Created by `docker login`; not in `.env` |
| Vultr API key | Only in your shell while installing, tearing down or using `access.sh` | Never in a file |
| Keycloak admin password | Keycloak's database (changed in the console); `.env` holds only the first-start value | See below |

General method: (1) take and verify a backup (`vm$ docker compose exec backup backup.sh --kind manual`, then `vm$ docker compose exec backup backup.sh --status` shows `"result": "ok"` and a `last_success` from the last minute; skip only if a secret leaked and the backup is slow, and say so in your log); (2) generate the new value with `openssl rand -hex 24` (letters and digits only, so no quoting problems); (3) change it **at its source first** (database, Keycloak, Vultr) while keeping the old value in your notes until step 6; (4) put the same value in `/opt/smb/.env` (`nano .env`, keep mode 600) and in your saved copy; (5) apply with the command in the table; (6) `./smoke.sh https://<domain> --compose-dir /opt/smb` must pass and the effect in the table must be checked; only then revoke the old value. **If step 5 or 6 fails**, undo in this order: put the old value back in `/opt/smb/.env` **and in your saved copy** (a typo is the usual cause: compare the lines), run the same apply command, run the smoke test, and escalate. Reinstating the old value at the source works for database passwords (set it again), Keycloak client secrets (paste the old one) and the restic key (the old key is still there until you remove it); a regenerated or deleted Object Storage key, registry credential or Vultr key cannot be brought back: create a new one and repeat.

Never put a secret in a command's arguments: they land in shell history and in process lists that other users can read. Where a command needs one, ask for it with `read -rs NEW` (no echo) and pass it on **standard input**, as the table shows (`printf` is built into the shell, so the secret never appears in any process's arguments).

**Some credentials have no overlap**: a database role password, a Keycloak client secret and an Object Storage key work only in their new form once changed, so do the change, the `.env` edit and the apply command back to back. Restic keys and registry credentials can exist in old and new form at the same time.

## Procedures

| Secret (`.env` key) | Procedure | Check | Impact |
|---|---|---|---|
| `SMB_APP_PASSWORD`, `SMB_OWNER_PASSWORD`, `KEYCLOAK_DB_PASSWORD` | `vm$ read -rs NEW` (paste the new value) then `vm$ printf "alter role smb_app password '%s';" "$NEW" \| docker compose exec -T postgres psql -U postgres` (role `smb_owner` or `keycloak` for the others; use only a value from `openssl rand -hex 24`, which cannot contain quotes). Then edit `.env` and run `vm$ docker compose up -d`. | smoke test passes; `docker compose ps` all healthy | `up -d` recreates the services whose settings changed, **including Postgres** (its environment lists these values): expect about a minute of errors. Data is in a volume and is kept. |
| `POSTGRES_PASSWORD` | `alter user postgres password` as above (as `postgres`), edit `.env`, `docker compose up -d` | `vm$ ./restore.sh --drill latest` works (this password is used only for restores) | Same short Postgres restart |
| `OIDC_CLIENT_SECRET`, `KEYCLOAK_ADMIN_CLIENT_SECRET` | The realm file sets these only when Keycloak first imports the realm, so change them in Keycloak: open the admin console ([access.md](access.md)), realm `smb` → Clients → `smb-app` (or `smb-admin`) → Credentials → "Regenerate" (or paste your new value). Then edit `.env` and run `docker compose up -d api worker`. | Sign in works; `Settings → Staff` loads (it uses the admin client) | Sign-in fails for the seconds between the console change and the restart. Existing sessions are not affected. |
| `KEYCLOAK_BOOTSTRAP_ADMIN_USER` / `_PASSWORD` | Only used when Keycloak first starts. Change the admin's password in the console (realm `master` → Users → admin → Credentials) and turn on a second factor for it; then edit `.env` so the two match (the file is only a record). | You can sign in to the console with the new password | none |
| `APP_SECRET_KEY` | Edit `.env`, `docker compose up -d api worker` | Sign in works | The key derives the anti-forgery (CSRF) tokens, so pages already open in a browser fail their next save until reloaded. **Sessions stay valid**: this does not sign anyone out (see "end all sessions" below). |
| `RESTIC_PASSWORD` | 1. `vm$ docker compose exec backup restic key list` (note the id of the key marked `*`: the current one). 2. `vm$ docker compose exec backup restic key add` and enter the new password twice. 3. Edit `.env`, `docker compose up -d backup`. 4. Check the **new** password works on its own: `docker compose exec backup restic snapshots` lists snapshots, and `docker compose exec backup backup.sh --kind manual` succeeds. 5. `docker compose exec backup restic key list`, then `docker compose exec backup restic key remove <old-key-id>` where the id is the one that is **not** marked `*`. 6. Store the new password offline. | Step 4 | none; old snapshots stay readable |
| `OBJECT_STORAGE_ACCESS_KEY` / `_SECRET_KEY` | Vultr console → your Object Storage subscription → regenerate keys, or create a new pair if offered (the subscription's overview shows the keys). Edit `.env`, `docker compose up -d backup`, run `backup.sh --kind manual`; only when it succeeds with the new keys, delete the old ones. | `vm$ docker compose exec backup restic snapshots` still lists the old snapshots | none |
| Registry credential | Vultr console → Container Registry → your registry → Docker/Kubernetes → Docker Credentials: set a validity, leave **Push Access unchecked**, generate. On the VM: `docker login <registry-host> -u <user>` (user and password from the generated config), then `docker compose pull` must succeed. Delete or let the old credential expire. | `docker compose pull` | none. **Credentials expire after the chosen validity: put the expiry date in your calendar, and choose a long validity.** |
| Inference API keys (when AI is installed) | Create the new key at the provider, update the matching variable in `.env`, `docker compose up -d api worker`, then revoke the old key at the provider (changing `.env` alone does not stop a leaked key) | An AI feature works | AI features pause for seconds |
| Vultr API key | Create a new one in the console (Account → API) with access control for your IP, test it with `access.sh`, then delete the old one | `access.sh` runs | none |

## SMTP provider credentials

See [SMTP secrets and rotation](email.md). Update the private `SMTP_URL` in both `.env` copies and the matching Keycloak Email settings; recreate API/worker and test application delivery, invitations and password reset before revoking the old credential. The shop selects its provider; use port 587 with STARTTLS or 465 with SSL. Vultr may block outgoing port 25.

## If a secret may have leaked

1. Rotate that secret now (above). If sessions or cookies may be stolen, also **end all sessions** so everyone must sign in again: `vm$ docker compose exec postgres psql -U postgres -d smb -c 'delete from sessions'`.
2. Review what the secret could reach and when: the application's **Audit page** shows actions inside the app only. For database, storage, registry and Vultr keys also check the Vultr console (account activity, Object Storage access, billing for unexpected resources) and the VM's `docker compose logs` for the period.
3. Look for things an attacker may have left behind: unknown users in Settings → Staff and in the Keycloak console, unknown API keys, SSH rules in the firewall group you did not create.
4. Write down what leaked, when, what was rotated, and what you checked. Escalate to the person in [README.md](README.md) ("Who to call").
