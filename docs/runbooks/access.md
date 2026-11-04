# Runbook: access (SSH, Keycloak admin console, logs)

Read [README.md](README.md) first (placeholders such as `<name>`, `<ip>`, `<domain>`, and how to find them).

## SSH to the VM

SSH is closed unless you opened it for your address. Two ways in.

**A. Vultr web console (no network rule needed).** Vultr console → Instances → your instance → "View Console". Log in as `root`; the password is shown on the instance's Overview page. Use it for emergencies: it is a root shell, so do the minimum. (The VM's own firewall `ufw` already allows 22, 80 and 443; the Vultr firewall group is the real gate, so you never need to touch `ufw`.)

**B. SSH from your computer, for your address only.**

1. Open the port with the helper (it finds your current IPv4 address, shows Terraform's plan, and asks for confirmation). **Accept only a plan that says `1 to add, 0 to change, 0 to destroy`** (one new `vultr_firewall_rule`; for `close`: `0 to add, 0 to change, N to destroy`, N = the number of SSH rules). Anything else: answer `no` and escalate:

   ```bash
   $ read -rs VULTR_API_KEY && export VULTR_API_KEY       # paste the key; nothing is shown
   $ deploy/scripts/access.sh open --name <name>
   ```

   To allow a different address use `--cidr 203.0.113.7/32` (one IPv4 address) or `--cidr 2001:db8::1/128` (one IPv6 address). The helper **keeps** addresses that were already allowed. Everyone behind the same office or home connection shares one public address, so allowing it allows them all to *try* (they still need the SSH key). Wider ranges are accepted but increase exposure: avoid them; a VPN with a fixed address is better.
2. Connect. The first time, SSH shows the host key fingerprint and asks to trust it; compare it with the real one by logging in through the Vultr web console (A) and running `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` (SSH normally offers the ED25519 key and says so; if it names another type such as ECDSA, use the matching file `/etc/ssh/ssh_host_ecdsa_key.pub`). Then:

   ```bash
   $ ssh -i install-<name>/id_ed25519 smb@<ip>
   vm$ cd /opt/smb
   ```

   The `smb` user can run Docker, which is effectively **root on this VM** (anyone who can run Docker can read all secrets and data). Treat the key accordingly.
3. **When finished, close the port again.** This removes **all** allowed addresses, including those of colleagues who are working on the VM right now: tell them first (they can re-run `open` afterwards):

   ```bash
   $ deploy/scripts/access.sh close --name <name>
   ```

   If the helper cannot be used (no Terraform state), delete the rules by hand: Vultr console → Firewall → the group for this installation → remove **every** TCP port 22 rule (one per allowed address and IP version; leave the 80 and 443 rules). Terraform will notice the difference at its next run and show it in its plan; accept only changes that re-create or remove SSH rules.

Keep SSH open for the whole session if you also need the Keycloak console below, and close it at the end.

## Where things are on the VM

| Path | What |
|---|---|
| `/opt/smb/compose.yml`, `.env` | The stack and its secrets (`.env` is mode 600, owned by `smb`, so you read it as `smb`) |
| `/opt/smb/VERSION`, `upgrade.log` | Current version and upgrade history |
| `/opt/smb/demo-logins/` | Demo installs only: the passwords written by `demo-logins` (folder mode 700, file mode 600). Store them, then delete the file ([install.md](install.md#demo-installs-only-logins-for-the-demo-staff-morgan-riley-and-the-others)) |
| `/opt/smb/smoke.sh`, `upgrade.sh`, `restore.sh` | Operating scripts |
| `systemctl status smb` | Shows whether the boot unit that starts the stack is active |

## Logs

```bash
vm$ cd /opt/smb
vm$ docker compose logs --tail 100 -f api            # one service; Ctrl-C stops following
vm$ docker compose logs --since 30m worker caddy
```

Application logs are JSON lines with a `request_id`. A user-visible error shows the same id. Find it (replace the id; it is a plain string, so use `-F`):

```bash
vm$ docker compose logs api | grep -F 'a1b2c3d4e5f6'
```

## Keycloak admin console (never public)

Caddy answers 404 for every path under `/auth/admin` and under `/auth/realms/master` (the administrator's own login), and Keycloak publishes no port, so the console is reachable only through an SSH tunnel to the container's address on the VM (the smoke test checks that nothing but Caddy publishes ports).

```bash
vm$ cd /opt/smb && docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' smb-prod-keycloak-1
```

It prints one address, for example `172.18.0.5`; use **that** address below. If it prints nothing, the container is not running (`docker compose ps`). On your computer (SSH must be open, step B):

```bash
$ ssh -i install-<name>/id_ed25519 -N -L 8081:172.18.0.5:8080 smb@<ip>      # replace 172.18.0.5; leave running
```

Open <http://localhost:8081/auth/admin/> in your browser (local port 8081 must be free). Sign in with `KEYCLOAK_BOOTSTRAP_ADMIN_USER` and `KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD` from `/opt/smb/.env` (`vm$ grep '^KEYCLOAK_BOOTSTRAP' /opt/smb/.env`). On first use change that password and turn on a second factor for the admin user (Users → admin → Credentials); afterwards the console password is the new one (record it in your password manager; the `.env` value is then out of date, see [rotate-secrets.md](rotate-secrets.md)). Choose realm `smb` for users and clients. Stop the tunnel with Ctrl-C and close SSH when done. **(first run: this tunnel has been checked only from inside the Docker network, not from a laptop)**

Scripted changes without a browser use the tool inside the container (replace `admin` with your `KEYCLOAK_BOOTSTRAP_ADMIN_USER` if you changed it):

```bash
vm$ docker compose exec keycloak /opt/keycloak/bin/kcadm.sh config credentials \
      --server http://localhost:8080/auth --realm master --user admin     # asks for the password
vm$ docker compose exec keycloak /opt/keycloak/bin/kcadm.sh get users -r smb -q email=person@example.com
```

## Database shell

```bash
vm$ cd /opt/smb && docker compose exec postgres psql -U postgres -d smb
```

This is the database superuser. Use read-only queries unless a runbook tells you otherwise: start every session with `begin read only;` and finish with `rollback;`, which makes accidental writes fail. The database holds every business's data on this installation, including customers' personal data: do not paste query results into chats, tickets or logs.
