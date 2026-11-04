# Runbook: incident

Read [README.md](README.md) first (placeholders, "Who to call"). Get onto the VM with [access.md](access.md), then always start the same way:

```bash
vm$ cd /opt/smb
vm$ docker compose ps --all                              # every service except migrate should say (healthy); migrate says Exited (0)
vm$ ./smoke.sh https://<domain> --compose-dir /opt/smb   # prints which check fails
vm$ docker compose logs --tail 100 <service>             # services: caddy api worker keycloak postgres backup
```

Write down the start time and each thing you try in your incident log (a text file or ticket; put its location in the README's "Who to call" note). Escalate to that person **30 minutes after you started the incident** (one clock for the whole incident, not per step), or at once if customer data may be lost or exposed, saying what you tried.

## The site does not load, or shows a certificate or 502/503 error

1. `docker compose ps --all`: find the service that is not healthy. 502/503 means Caddy is up but a service behind it (`api` or `keycloak`) is not answering: restart **the one that is unhealthy** (`docker compose restart <service>`), wait a minute, run the smoke test. If both are healthy, look at `docker compose logs --tail 100 caddy` for the failing request. If both are unhealthy, restart `postgres` first, then `keycloak`, then `api`.
2. Caddy not running or restarting: `docker compose logs --tail 100 caddy`. Usual cause: DNS or ports (Let's Encrypt cannot validate). Check `dig +short A <domain>` equals the VM's IPv4 and `dig +short AAAA <domain>` is empty or equals its IPv6; check the Vultr firewall group of this installation (Vultr console → Firewall) allows TCP 80 and 443 from anywhere.
3. A certificate error while Caddy runs normally: `docker compose logs --tail 200 caddy | grep -iE 'tls|acme|certificate'` shows why issuing or renewing failed (usually DNS or port 80/443 as in step 2). Fix that, wait 5 minutes for Caddy to retry, reload. Still failing after an hour: escalate (Let's Encrypt limits retries).
4. Page loads but is blank: open the browser's developer console. A "Content-Security-Policy" message means the page and server policy disagree. Do not weaken the policy; report it.

## Sign-in fails

- "Invalid parameter: redirect_uri": the public address (scheme and host only, for example `https://app.example.com`, which is what the browser shows before the first `/`) differs from what the application or Keycloak has. The approved address is `https://<domain>`. Check `grep '^PUBLIC_URL=' /opt/smb/.env` equals exactly that (fix `.env` if not, then `docker compose up -d api worker keycloak caddy` and repeat the same line in your saved copy). If it does, the Keycloak client has a stale address: in the admin console ([access.md](access.md)) realm `smb` → Clients → `smb-app` set "Valid redirect URIs" to exactly `https://<domain>/api/v1/auth/callback` (replace any old entry), "Web origins" to `https://<domain>`, and "Valid post logout redirect URIs" to `https://<domain>/*`.
- Keycloak answers 503 for up to a minute after a restart: wait. Longer: `docker compose logs --tail 100 keycloak`.
- A user is locked for 15 minutes after 5 failures. To unlock: Keycloak admin console → realm `smb` → Users → the user → "Unlock" (or delete the temporary lock).
- Lost authenticator app: **first verify who is asking** (call the person back on a number you already had, or have the business owner confirm); then admin console → Users → the user → Credentials → delete the OTP credential, and under Details add the required action "Configure OTP". They set up a new one at next sign-in.

## A service is down

```bash
vm$ docker compose up -d              # starts stopped services; it also recreates any service whose configuration changed since it was created (check `git`/your notes: nobody should have edited .env or compose.yml without applying it)
vm$ docker compose restart <service>  # one service
```

If `api` is unhealthy right after an upgrade, see [upgrade.md](upgrade.md). If `postgres` will not start, **do not delete its volume**: read `docker compose logs postgres` and, if data is damaged, follow [backup-restore.md](backup-restore.md).

## Disk is full

```bash
vm$ df -h                       # all filesystems; look at the one holding /var/lib/docker (usually /)
vm$ docker system df            # what Docker uses
```

1. Remove unused images: `docker image prune -a`. This VM only runs this project; it removes images no container uses (old versions you rolled back from, which `docker compose pull` can fetch again). Only do it if the registry login works (`docker compose pull` succeeds), or you cannot get them back.
2. Container logs are capped (5 files of 10 MB per service).
3. The `backup_state` volume holds a restic cache and temporary dumps; its size is shown by `docker system df -v`. The backups themselves are in Object Storage. Do not delete files in it by hand.
4. If the database volume itself fills the disk, resize the VM in the Vultr console (Instance → Settings → Change plan; choose a plan with a bigger disk; this restarts the VM and cannot be shrunk later). Plan this early: at 80 % full.

## Background jobs seem stuck (e-mails not sent, approved actions not executed)

```bash
vm$ docker compose ps worker                                   # must be (healthy)
vm$ docker compose logs --tail 50 worker
vm$ docker compose exec postgres psql -U postgres -d smb -c \
    "select status, count(*), min(run_at) as oldest_run_at, min(locked_until) filter (where status='running') as oldest_lock from jobs group by status order by 1"
```

- `queued` jobs whose `oldest_run_at` is more than 10 minutes in the past while the worker is healthy: restart it (`docker compose restart worker`) and look at the worker log for the same error repeating.
- `running` jobs whose `oldest_lock` is in the past: the worker releases expired locks within a minute; wait, then re-run the query.
- `dead` jobs (failed 8 times): see why: `docker compose exec postgres psql -U postgres -d smb -c "select kind, last_error from jobs where status='dead' order by updated_at desc limit 10"`. Fix the cause (for example a bad address). There is no retry button; ask the developers before editing rows.

## A message shows "unknown" delivery

The system could not tell whether a message was sent (for example the process stopped while talking to the mail provider). It never resends on its own. In the app, Approvals → the proposal → Deliveries lists the message with status "unknown". Find out whether it arrived (ask the recipient, or check the mail provider's log). Then record the decision with the API call `POST /api/v1/notifications/outbox/<message-id>/resolve` and body `{"resolution": "delivered"}` (it arrived) or `{"resolution": "retry"}` (send it again). It needs a signed-in user with permission `notifications.outbox.resolve` (owner or admin) and the CSRF header, so in practice: ask the developers to run it for you until the app has a button for it. If you cannot find out, choose `retry` only when a duplicate is harmless. Details: `docs/roadmap/modules/M10-notifications.md`.

## Backups failing

`vm$ docker compose exec backup backup.sh --status`, then the troubleshooting table in [backup-restore.md](backup-restore.md). A failing backup is urgent: it is the only protection against data loss. Escalate the same day.

## AI provider outage (applies once the AI layer is installed)

Staff can keep working: every AI feature is optional and nothing is sent automatically (every action needs a person's approval). To stop all outside AI calls for a business, the owner sets the AI policy to "strict private" in Settings → Policies; check it took effect by trying an AI feature (it should say AI is off). Queued AI work fails and is not retried automatically. Record the outage times.

## After the incident

Run the smoke test and `backup.sh --status`; both must be clean. If either still fails, the incident is not over: escalate. Then write down in the incident log: what happened and when, what fixed it, and what would make it visible sooner next time.
