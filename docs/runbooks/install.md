# Runbook: install on a new Vultr VM

Time: about 30 minutes, most of it waiting for DNS and first boot. Result: HTTPS site at `https://<domain>`, an owner who can sign in with a password and an authenticator app, a first backup in Object Storage, and a passing smoke test.

## How to read the commands

- Run every command **from the root of this repository** (the folder that contains `Makefile` and `deploy/`), unless it says `vm$`.
- `$ command` runs on your own computer; `vm$ command` runs on the VM (section 6 shows how to connect). Do not type the `$` or `vm$`.
- Replace placeholders with your own value: words in angle brackets (`<name>` is the installation name, for example `acme`; `<domain>` its public host name; `<ip>` the VM's IPv4 address), `YOUR.PUBLIC.IP.ADDRESS`, and the sample values in the examples (`acme`, `app.example.com`, `owner@example.com`, `smb-ro`, `smb-backups`). Never type the angle brackets. Names written in capitals such as `VULTR_API_KEY`, `VERSION`, `REGISTRY` and `PLATFORM` are fixed names: keep them exactly.
- Commands are for **bash or zsh** (the macOS and Linux defaults).
- Docker Desktop (or another Docker daemon) must be **running** on your computer.

## 1. What you need before you start

| Need | How to get it |
|---|---|
| Vultr account with credit | Customer's own account (or the demo account). In the console, Billing → set alerts before you create anything. |
| Vultr API key | Console → Account → API → enable it. **Under "Access Control" first add your public IP address** (Vultr's API keys cannot be limited to products, so the address list is the protection; with no address allowed the key does not work). Copy the key. Used only in your shell. |
| Container Registry | Console → Container Registry → create one (name it, for example, `smb`; choose the same region as the VM). Note its host and namespace, shown on its page (for example `sjc.vultrcr.com/smb`). Credentials: open the registry → **Docker/Kubernetes** → **Docker Credentials**, set a **validity** (choose long; they stop working when it ends, and the VM pulls at every upgrade), tick **Push Access** only for the push credential, and click **Generate Docker Config JSON**. Generate two: one with Push Access (used once, section 2) and one without (read-only, used by the installer). The file contains the user name and password: open it, and note that its `auths` entry is `base64(username:password)` (`echo '<that value>' \| base64 -d`). The "user name" of the **read-only** credential is `--registry-user` (replace the sample `smb-ro`), its "password" is `REGISTRY_TOKEN`. Write each credential's **expiry date** in your calendar: when it passes, upgrades stop working until you renew it ([rotate-secrets.md](rotate-secrets.md)). *Vultr's docs describe these steps; this project has not run them on a real account yet.* |
| Object Storage subscription and a bucket | Console → Products → Object Storage → add a subscription (location Silicon Valley for the default), create a bucket (for example `smb-backups`). On the subscription's **Overview** page note the hostname (Silicon Valley: `sjc1.vultrobjects.com`, so the endpoint is `https://sjc1.vultrobjects.com`) and the **access key** and **secret key**. |
| A domain you can edit | You will create one A and one AAAA record at your DNS provider. Reserved example domains such as `app.acme.example` cannot get public certificates. For demos/tests without a domain, see the `--demo --domain auto` section below. |
| Your public IP address | `curl -4 -s https://ifconfig.me` prints your IPv4 address; use it as `YOUR.PUBLIC.IP.ADDRESS/32` (`/32` = this one address). The runbooks connect to the VM's IPv4 address, so the IPv4 address is the one that must be allowed. Only if your connection has no IPv4 at all, use your IPv6 address with `/128` and connect to the VM's IPv6 address (`terraform output -raw ipv6`) wherever `<ip>` appears. |
| Tools on your computer | `terraform`, `ssh`, `scp`, `ssh-keygen`, `curl`, `openssl`, `jq`, `python3`, `docker`, GNU `make`. The installer checks all but `docker` and `make`. Also handy: `dig` (DNS checks) and `nmap` (port check on another computer). |
| This repository at the version you want to install | `git`-tag or folder; the image version you build in section 2 must match what you install (`--version`). |

## 2. Build and push the images (once per version)

```bash
$ make images VERSION=0.1.0 REGISTRY=sjc.vultrcr.com/smb PLATFORM=linux/amd64
$ docker login sjc.vultrcr.com            # use the credential that may push
$ docker push sjc.vultrcr.com/smb/smb-api:0.1.0
$ docker push sjc.vultrcr.com/smb/smb-web:0.1.0
$ docker push sjc.vultrcr.com/smb/smb-backup:0.1.0
```

Replace `sjc.vultrcr.com/smb` with your registry host and namespace (and `sjc.vultrcr.com` with the host only). When the pushes are done, remove the push credential from your computer: `docker logout sjc.vultrcr.com`. `PLATFORM=linux/amd64` matters: the VM is x86-64. On an Apple-silicon Mac this build is slower and needs Docker Desktop's cross-platform support (enabled by default).

## 3. Check what the installer will do (no cost, no changes)

Use your own values. This example shows the shape; every option below is required except those marked optional in section 4.

```bash
$ deploy/scripts/install.sh --dry-run \
    --name acme --domain app.example.com --version 0.1.0 \
    --registry sjc.vultrcr.com/smb --registry-user smb-ro \
    --owner-email owner@example.com --owner-name "Alex Owner" \
    --business-name "Acme Cycles" --slug acme-cycles --timezone America/Denver \
    --object-storage-url https://sjc1.vultrobjects.com --bucket smb-backups \
    --admin-cidr YOUR.PUBLIC.IP.ADDRESS/32
```

Rules: `--name` is 2–31 lowercase letters, digits or hyphens, starting with a letter; `--slug` is lowercase letters, digits and hyphens (it appears in public links); `--timezone` is an IANA name such as `America/Denver` or `Europe/London`. The dry run prints all eight steps and creates nothing. Fix any "missing" message it reports.

## 4. Run the installer

```bash
$ read -rs VULTR_API_KEY && export VULTR_API_KEY                      # asks for the value without showing it
$ read -rs REGISTRY_TOKEN && export REGISTRY_TOKEN                      # the read-only registry password
$ read -rs OBJECT_STORAGE_ACCESS_KEY && export OBJECT_STORAGE_ACCESS_KEY
$ read -rs OBJECT_STORAGE_SECRET_KEY && export OBJECT_STORAGE_SECRET_KEY
$ deploy/scripts/install.sh \
    --name acme --domain app.example.com --version 0.1.0 \
    --registry sjc.vultrcr.com/smb --registry-user smb-ro \
    --owner-email owner@example.com --owner-name "Alex Owner" \
    --business-name "Acme Cycles" --slug acme-cycles --timezone America/Denver \
    --object-storage-url https://sjc1.vultrobjects.com --bucket smb-backups \
    --admin-cidr YOUR.PUBLIC.IP.ADDRESS/32
```

The four variables exist only in this terminal window (nothing is saved to history or a file by you); the installer writes the Object Storage keys into the installation's `.env` because the backup service needs them there, mode 600, and the installation's `.env` is the only file that holds them (two copies: yours in `install-<name>/` and the one the installer uploads to `/opt/smb`, which it sets to mode 600). That is the dry-run command without `--dry-run`. Optional: `--plan` (default `vhp-4c-8gb-amd`; the minimum is `vhp-2c-4gb-amd`), `--region` (default `sjc`), `--out-dir` (where `install-<name>/` is created; default is the current folder; if you use it, replace `install-<name>/` by `<out-dir>/install-<name>/` in every path below and in the other runbooks), `--resume` (continue a run that stopped part-way, see Troubleshooting), `--admin-cidr` again for more addresses, `--close-ssh` (see step 8).

`--admin-cidr` is **required**: the installer works over SSH, and the firewall opens port 22 only for these addresses. `--version` must be the version you pushed in section 2.

What it does, and what you must do at each pause:

1. **Preflight** checks tools and environment variables.
2. **Secrets**: creates `install-<name>/` containing `.env` (mode 600, generated passwords) and a new SSH key. It then prints the **restic password once**. Write it down somewhere offline (password manager or paper). Without it the backups cannot be read. Press Enter to continue.
3. **Terraform** creates the VM, the firewall group (80 and 443 open to everyone, 22 only for `--admin-cidr`) and the SSH key. State is kept in `install-<name>/terraform.tfstate`.
4. **DNS (your action)**: it prints the VM's IPv4 and IPv6 addresses. At your DNS provider create `A <domain> → IPv4` and `AAAA <domain> → IPv6` (TTL 300 seconds). The installer waits up to 30 minutes for the **A** record to resolve to the VM's IPv4. It does not check the AAAA record, so check it yourself: `dig +short AAAA <domain>` must print the printed IPv6 address. **If the AAAA record is wrong, delete it** (a wrong AAAA breaks HTTPS for visitors who have IPv6; no AAAA record is fine). Resolvers keep old answers for the record's TTL (300 seconds if you followed the advice), so re-check after that time before trusting the HTTPS result.
5. **First boot and upload**: waits for cloud-init to install Docker (`/opt/smb/READY`), uploads the stack and `.env`, logs in to the registry, pulls the images and starts everything.
6. **Bootstrap**: waits for `https://<domain>/api/v1/readyz` (Caddy fetches a Let's Encrypt certificate on first request; this needs DNS to be correct), then creates the business and the owner's login in Keycloak. It delivers the **temporary password immediately on the controlling terminal**, before backup, smoke test or firewall changes; redirected installer output does not contain it. Use an interactive terminal and store the password when it appears. Without a controlling terminal, bootstrap refuses to create credentials.
7. **Smoke test and first backup**: runs a backup to Object Storage, waits up to 120 seconds for Docker to report the backup service healthy, then runs `smoke.sh`. A successful backup can precede Docker's next healthcheck; the wait handles that delay. A timeout stops the install before smoke; inspect backup logs and `docker compose ps` before resuming.
8. **Finish** prints the owner's first-login instructions. With `--close-ssh` it also closes port 22 again. **Do not use `--close-ssh` if you want to follow section 6 right away**: the checks there need SSH. Close it later with `deploy/scripts/access.sh close --name <name>`. Without the flag, SSH stays open for your address until you close it ([access.md](access.md)).

When the installer has finished (or you give up), clear the secrets from your terminal: `unset VULTR_API_KEY REGISTRY_TOKEN OBJECT_STORAGE_ACCESS_KEY OBJECT_STORAGE_SECRET_KEY`, or close the window.

### Demo or test install without a domain

After pushing the images and setting the four secret environment variables above (an automated agent should receive them through its secret environment), run:

```bash
deploy/scripts/install.sh --demo --domain auto \
  --name smb-demo --version 0.1.0 \
  --registry sjc.vultrcr.com/smb --registry-user smb-ro \
  --timezone America/Denver \
  --object-storage-url https://sjc1.vultrobjects.com --bucket smb-backups \
  --admin-cidr YOUR.PUBLIC.IP.ADDRESS/32
```

Replace the registry, bucket and admin address with your values. Add `--dry-run` to preview all eight steps without changes. No interactive terminal or confirmation input is required. `--demo` uses only the synthetic fixture businesses Example Cycle Works and Example Garden Store and their existing seed/login safeguards; omit `--owner-email`, `--owner-name`, `--business-name` and `--slug` (overrides are refused). The seed uses the fixtures' timezones; `--timezone` remains a required installer argument. `DATA_PROFILE=regulated` is refused, including in a resumed `.env`.

`--domain auto` is allowed only with `--demo`. Secrets are initially generated with `pending.invalid` as the domain. After Terraform returns the IPv4, the installer uses its dashed form: `203.0.113.10` becomes `203-0-113-10.sslip.io`. It rewrites `DOMAIN` and `PUBLIC_URL` in `install-smb-demo/.env`, saves `install-smb-demo/resolved-domain` for `--resume`, and skips creating DNS records. It still checks that DNS resolves to the VM's IPv4 before uploading anything. **sslip.io is a third-party wildcard DNS service suitable only for demos/tests**; DNS availability and public certificate issuance depend on external services. For a customer installation use your own domain and the regular bootstrap flow. `--demo --domain demo.your-domain.com` also works, with the usual DNS setup.

The demo path runs `seed`, then the `demo-logins` command described below for Morgan and Riley. Login passwords stay in `/opt/smb/demo-logins/logins.txt` on the VM (folder mode 700, file mode 600); the installer prints only its location and never prints or copies its contents. The restic password also stays in the private `.env`, with no printout or pause: store that file safely offline so backups remain restorable. Sign-in still requires TOTP. Backups to Object Storage, smoke tests, firewall configuration and optional `--close-ssh` run as usual. Retrieve the login file privately before closing SSH, or reopen SSH with [access.md](access.md).

If interrupted, repeat the same command with `--resume`. It reuses the saved domain and secrets; when both demo businesses already exist it skips seed and reruns the idempotent `demo-logins` checks without resetting passwords. Those checks still refuse businesses whose name or seeded identity does not match the fixtures. If only one business was seeded before an interruption, seed refuses to overwrite existing data; inspect that partial seed before retrying.

### Optional: enable Vultr AI during installation

AI settings are optional; omitting them keeps the existing default behavior. Add `--ai-routes /path/to/operator-routes.yaml` to the install command to upload that file as `/opt/smb/config/ai-routes.yaml`. Supply a reviewed route file with the desired models and a Vultr provider using `https://api.vultrinference.com/v1` and `api_key_env: VULTR_INFERENCE_API_KEY`; put only the environment variable name in the route file, never the key itself. The installer validates locally with the backend loader when `backend/.venv` exists. Otherwise it requires Python with the project's existing PyYAML and checks YAML syntax and the `providers` and `routes` mappings. Invalid or unreadable files stop the install before provisioning. This validation also runs with `--dry-run`.

Read the inference key privately before running the installer:

```bash
$ read -rs VULTR_INFERENCE_API_KEY && export VULTR_INFERENCE_API_KEY
```

When that variable is nonempty, the installer writes it to the local `install-<name>/.env` and the VM's `/opt/smb/.env`, both mode 600, and adds `api.vultrinference.com` to `EGRESS_ALLOWLIST` while keeping existing hosts. It never prints the key. Clear the shell variable afterwards with `unset VULTR_INFERENCE_API_KEY`.

On `--resume`, an existing route file is preserved unless you pass `--ai-routes` again. Without the key environment variable, saved AI credentials and egress settings are preserved too. A normal upgrade also preserves the operator's route file. Keep any manual `.env` edits equal in both copies before resuming, because the installer uploads the local copy.

### Enable AI on an existing installation

Open SSH for your address if needed ([access.md](access.md)). Upload a reviewed route file from your computer, then edit both environment files privately:

```bash
$ scp -i install-<name>/id_ed25519 /path/to/operator-routes.yaml smb@<ip>:/opt/smb/config/ai-routes.yaml
$ ${EDITOR:-vi} install-<name>/.env
$ chmod 600 install-<name>/.env
$ ssh -i install-<name>/id_ed25519 smb@<ip>
vm$ ${EDITOR:-vi} /opt/smb/.env
vm$ chmod 600 /opt/smb/.env
vm$ chmod 644 /opt/smb/config/ai-routes.yaml
vm$ cd /opt/smb
vm$ docker compose up -d api worker
```

In **both** `.env` copies, set `VULTR_INFERENCE_API_KEY` to the same inference key (use a single-quoted value to keep dollar signs literal). Add `api.vultrinference.com` to the comma-separated `EGRESS_ALLOWLIST`; keep any existing hosts. Do not paste keys into shell commands or logs. The route file, key and allowed host are all needed; restarting with `up -d` recreates api/worker when their environment changes. A configured route still needs the selected model to be available and the business's AI policy to allow it. Verify a real AI action in the application separately; local YAML validation and smoke do not prove inference connectivity.

### Move a running installation to your own domain

Used on 2026-11-07 to move the demo from `45-63-85-83.sslip.io` to `tandemai.open-dl.com`. Downtime is about a minute.

1. Create `A <domain> → <ip>` at your DNS provider and wait until `dig +short A <domain>` prints the VM's IPv4. With
   Cloudflare choose **DNS only**: through the proxy every visitor would share Cloudflare's addresses, and the public
   chat's per-address limits would block real users.
2. Open SSH (`deploy/scripts/access.sh open --name <name>`), then on the VM: `docker compose exec backup backup.sh --kind
   manual` (check the result with `docker compose exec backup backup.sh --status`) and keep a copy of `.env`.
3. Keycloak imports the realm only once, so update the web client yourself (inside the keycloak container, signed in
   with `kcadm.sh config credentials` as in [access.md](access.md)): for client `smb-app` set `redirectUris` to
   `https://<domain>/api/v1/auth/callback`, `webOrigins` to `https://<domain>` and the attribute
   `post.logout.redirect.uris` to `https://<domain>/*`.
4. In `/opt/smb/.env` set `DOMAIN=<domain>` and `PUBLIC_URL=https://<domain>`, run `docker compose up -d` (Caddy gets a
   Let's Encrypt certificate on the first request), then `./smoke.sh https://<domain> --compose-dir /opt/smb`.
5. Close SSH, update `install-<name>/.env` and `install-<name>/resolved-domain` on your computer, and run
   `terraform plan` (the domain is not a Terraform input, so it must report no changes). The old address stops working;
   links already sent by e-mail point to it.

## 5. First login (the owner)

1. Open `https://<domain>` and sign in. Username: the owner's email. Password: the temporary one printed by the installer.
2. Keycloak requires a **new password** (at least 12 characters).
3. Keycloak then shows a QR code: scan it with an authenticator app (any TOTP app) and enter the six-digit code. This second factor is mandatory for everyone.
4. The owner lands on the Today page of their business.

**Inviting colleagues:** Settings → Staff → Invite. Two modes:

- **Without e-mail (the default after install, `SMTP_URL` empty):** the app creates the person's login and shows a **one-time temporary password** in the invite dialog. Give it to them securely; they must change it and set up an authenticator app at first sign-in.
- **With e-mail:** configure the shop's chosen provider in `SMTP_URL` (and optional `SMTP_FROM`) in both private `.env` copies, then configure the **same decoded values manually in Keycloak**. Follow [SMTP setup and Keycloak test steps](email.md). Use submission port 587 with STARTTLS (or 465 with implicit TLS); Vultr may block outgoing port 25. Realm imports do not update existing realms, so verify Keycloak mail settings on upgrades too. System → Send test e-mail tests application delivery; invitations and Forgot password must each be tested separately in Keycloak. **Production provider and Keycloak e-mail delivery have not been verified locally.**


### Demo installs only: logins for the demo staff (Morgan, Riley and the others)

Skip this on a customer's installation. A **demo** VM is seeded with the synthetic businesses Example Cycle Works and Example Garden Store (`seed`; the [demo script](../demo-script.md) resets them with `reset-demo`). The seed creates their staff (Morgan Example the owner, Riley Example the admin, and six more) as users that claim a login the first time someone signs in with their e-mail address. **Keycloak has no accounts for them**: only `bootstrap-business` and invitations create accounts, and `dev-account` refuses production. Until you run the command below nobody can sign in as Morgan or Riley, and the demo needs both signed in at once (Morgan creates a proposal, Riley approves it).

```bash
vm$ cd /opt/smb
vm$ install -d -m 700 demo-logins                  # a private folder next to .env for the passwords
vm$ docker compose run --rm -T --user "$(id -u):$(id -g)" \
      -v "$PWD/demo-logins:/opt/smb/demo-logins" api \
      python -m app.cli demo-logins --out /opt/smb/demo-logins/logins.txt \
      --email morgan@example.com --email riley@example.com
```

It prints the path of the file and the e-mail addresses it made logins for, **never a password**. The file `/opt/smb/demo-logins/logins.txt` has mode 600 and belongs to you; each line is an e-mail address and a password. Read the passwords (`vm$ cat /opt/smb/demo-logins/logins.txt`), store them in your password manager and delete the file (`vm$ rm /opt/smb/demo-logins/logins.txt`). It is not part of the backups. On a laptop with `make dev` the same command is `make demo-logins` (the file is `.demo-logins/logins.txt`, ignored by git).

- **Why the folder and `--user`:** the api container's file system is read-only and it runs as another user, so it writes into a folder mounted from the VM, as you. Without the mounted folder the command stops before it creates anything ("the folder ... does not exist").
- **Who:** leave out the two `--email` options to create logins for all eight demo staff; `--business example-cycle-works` limits it to one business.
- **First sign-in:** open `https://<domain>`, enter the e-mail address and the password. The password is permanent: Keycloak does **not** ask for a new one (add `--temporary-password` if you want it to). It does ask for an authenticator app, as for everyone: scan the QR code once (one phone can hold both accounts). Use two browser profiles, or a private window for the second person, so both stay signed in.
- **Safe to repeat:** an account that already exists is left alone ("already had a login") and the file is not touched. If a password is lost: `... demo-logins --out /opt/smb/demo-logins/logins.txt --email riley@example.com --reset-passwords` issues a new one, and the other lines of the file stay. The person's authenticator app is kept. `reset-demo` does not remove the logins (it recreates the businesses, not the users or the Keycloak accounts).
- **What it refuses:** it creates accounts only for the demo staff listed in `data/fixtures.json` (example.com addresses), of the businesses `example-cycle-works` and `example-garden-store` as the seed created them (matched by name and by the id the seed gives them). Any other business name, any other person, a business that only borrows a demo name, and an installation with `DATA_PROFILE=regulated` are refused, so it cannot create a login for a customer's staff. Run `seed` first ("does not exist; run `seed` first").
- **Output lines:** `not usable: <e-mail>: ...` means the person signed in before and their Keycloak account is gone or different, so a new account could not sign in either (fix it in the Keycloak console, [access.md](access.md)); the command then ends with exit status 1. `skipped: ...` names a person who is not an active member of the business (removed, or the user is disabled).

## 6. Verify

Connect to the VM (this works only while SSH is open for your address):

```bash
$ ssh -i install-<name>/id_ed25519 smb@<ip>
vm$ cd /opt/smb && ./smoke.sh https://<domain> --compose-dir /opt/smb
```

Expected: every line `PASS` and `SMOKE PASSED`. Then check the backup:

```bash
vm$ docker compose exec backup backup.sh --status
```

`"result": "ok"` and a `last_success` timestamp (UTC, `YYYY-MM-DDTHH:MM:SSZ`) from the last few minutes of the install mean the first backup reached Object Storage. Also look at the bucket in the Vultr console: it should contain `data`, `index`, `keys` and `snapshots` folders. This proves the backup was written, not that it can be restored: run the drill in [backup-restore.md](backup-restore.md) once after the install.

From a computer whose public address is **not** in any `--admin-cidr` (a phone hotspot is an easy way; confirm with `curl -s https://ifconfig.me` that its address differs), confirm only web ports are open: `nmap -Pn -p- <ipv4>` (if you allowed a wider range than `/32`, make sure the scanning computer's address is outside it). This checks TCP only. It should show 80 and 443 open and 22 filtered or closed. For IPv6 (only if that computer has IPv6): `nmap -6 -Pn -p- <ipv6>`.

Finally close SSH if it is still open ([access.md](access.md)).

## 7. Keep safe

- `install-<name>/` (secrets, SSH key, Terraform state): copy the whole folder to an **encrypted** place away from this computer (password manager attachment or encrypted drive). If this computer is lost, the database passwords, the SSH key and the Terraform state go with it: without the state, Terraform can no longer manage or destroy the VM (you would delete the resources in the Vultr console), and a restore on a new machine needs the `.env` ([backup-restore.md](backup-restore.md)). The folder is ignored by git; never commit it.
- The restic password (offline, written down or in a password manager).
- Write the name and phone number of the person responsible for this installation into the owner's handover note and into [README.md](README.md) ("Who to call").
- If the owner loses the temporary password before first login, or later their authenticator app: [incident.md](incident.md) ("Sign-in fails"); for a lost **password** an administrator resets it in the Keycloak console (Users → the user → Credentials → Reset password, temporary); a lost **authenticator app** is a separate procedure.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| "--admin-cidr is required" | Add `--admin-cidr YOUR.PUBLIC.IP.ADDRESS/32` (section 1). |
| "DNS ... does not resolve" | Records not created or not propagated. Check with `dig +short A <domain>`. Then repeat the **same command with `--resume`**: it reuses `install-<name>/` and continues. Terraform is idempotent, so it should report no changes; if it proposes to change or replace the VM, stop and read what it says before confirming. |
| "VM is not ready after 15 minutes" | cloud-init still running or failed. Vultr console → Instances → the VM → "View Console", log in as `root` (password on the instance's Overview page), then `tail -f /var/log/cloud-init-output.log`. When it finishes, repeat the same command with `--resume`. |
| You want to give up, or the install failed and cannot be repaired | Follow [teardown.md](teardown.md) (it also covers half-finished installs). A VM that exists is billing. |
| `docker login` fails | Wrong registry host, user or token. The host is the part before the first `/` of `--registry`. |
| Browser shows a certificate warning | DNS does not point to the VM yet, a wrong AAAA record exists, or ports 80/443 are blocked, so Caddy cannot get a certificate. On the VM: `cd /opt/smb && docker compose logs --tail 100 caddy`. |
| Smoke test fails on "last backup ok" | The backup could not reach Object Storage: wrong endpoint, bucket or keys. On the VM: `cd /opt/smb && docker compose logs --tail 50 backup`. Edit `/opt/smb/.env` on the VM, **and the same line in your local `install-<name>/.env`** so the two stay equal, then on the VM `docker compose up -d backup`, then `docker compose exec backup backup.sh --kind manual` and `docker compose exec backup backup.sh --status` (must say `"result": "ok"`), then run the smoke test again. |
| Keycloak page says "Invalid parameter: redirect_uri" | `PUBLIC_URL` in `.env` differs from the address in the browser. They must be identical, including `https://`. Fix it in `/opt/smb/.env` (and your local copy) and run `docker compose up -d` on the VM. Keycloak takes the address from `PUBLIC_URL` only when it first imports the realm: if it was wrong at the first start, also correct the client `smb-app` ("Valid redirect URIs" `<PUBLIC_URL>/api/v1/auth/callback`, "Web origins" `<PUBLIC_URL>`) in the Keycloak admin console ([access.md](access.md)). |
| SSH refused after `--close-ssh` | Expected: reopen your address as described in [access.md](access.md), or use the Vultr web console. |
