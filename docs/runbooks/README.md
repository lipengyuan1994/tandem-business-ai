# Runbooks

Operating instructions for a Tandem Business AI installation. Each one can be followed by a person or a coding agent that has only that page and shell access.

| Runbook | When |
|---|---|
| [install.md](install.md) | New installation (demo or customer) on a Vultr VM |
| [upgrade.md](upgrade.md) | Move an installation to a new version; roll back |
| [backup-restore.md](backup-restore.md) | Manual backup, restore drill, real restore |
| [regulated-profile.md](regulated-profile.md) | Configure regulated retention, read audit, agreements and limits |
| [email.md](email.md) | Configure SMTP credentials and Keycloak mail; verify invitations/password resets |
| [rotate-secrets.md](rotate-secrets.md) | Change database, OIDC, restic or API-key secrets |
| [incident.md](incident.md) | Something is down or behaving badly |
| [access.md](access.md) | SSH, Keycloak admin console, logs |
| [teardown.md](teardown.md) | Destroy an installation and check billing |

**Status of these procedures (2026-11-03):** the Compose stack, `smoke.sh`, `upgrade.sh`, `restore.sh`, `backup.sh` and the backup test were run locally against the production images (see `PRE_EVENT_WORK.md`). `install.sh`, the Terraform module and the cloud-init file were validated and dry-run only: no VM has been created yet. Steps that involve Vultr itself are marked **(first run: P4-09)** and are the most likely to need correction the first time.

## Conventions and look-ups (apply to every runbook)

- `$ command` runs on **your own computer**, from the **root of this repository** (the folder with `Makefile` and `deploy/`). `vm$ command` runs **on the VM**. Never type the `$` or `vm$`.
- Words in angle brackets are placeholders. **Replace the whole thing including the brackets** (a literal `<` is a shell operator). Example: `smoke.sh https://<domain>` becomes `smoke.sh https://app.example.com`.
- On the VM, every Docker Compose command needs to run in `/opt/smb`. Each runbook says `vm$ cd /opt/smb` first; if you open a new SSH session, do it again.
- Always use `docker compose` (with a space), never the old `docker-compose`.

| Placeholder | Meaning | How to find it |
|---|---|---|
| `<name>` | Installation name given to `install.sh --name` | The folder `install-<name>/` on the computer you installed from (if you used `--out-dir`, it is `<out-dir>/install-<name>/`; use that path wherever a runbook writes `install-<name>/`) |
| `<domain>` | Public host name | `grep '^DOMAIN=' install-<name>/.env` |
| `<ip>` | VM's IPv4 address | `terraform -chdir=deploy/terraform/envs/demo output -state=$PWD/install-<name>/terraform.tfstate -raw ipv4` (or the Vultr console, Instances) |
| `<version>` | Image version | `vm$ cat /opt/smb/VERSION` shows what runs now |

**Secrets hygiene.** Do not type secrets into commands that other users could see in a process list or that your shell saves in its history. For API keys use `read -rs VULTR_API_KEY && export VULTR_API_KEY` (it asks without echoing). Files that legitimately hold secrets are the installation's `.env` (on the VM and your saved copy), both mode 600.

**Who to call.** Write the name and phone number of the person responsible for this installation here, and in the owner's handover note, when you install: ______________________. The runbooks say "escalate" when a problem is beyond them; this is who that is.

Host checks, alert delivery, external uptime ownership, journal retention, and project-only disk housekeeping: [monitoring.md](monitoring.md).
