# Runbook: tear down an installation

Read [README.md](README.md) first (placeholders, "Who to call"). Use this when a demo ends, a customer leaves, credits run low, **or an install failed half-way** (resources may already be billing: destroy them here, then start again).

**Destroying is permanent.** Work through the steps in order. The Vultr API key is the last thing you delete (step 5), because steps 3 and 4 need it.

## 0. Identify exactly what you are removing

(If the install failed so early that `install-<name>/` has no `terraform.tfstate`, or the folder is gone: do the identification in the Vultr console and use "Without Terraform state" at the end of step 3.)

```bash
$ ls install-<name>/                  # must exist: terraform.tfstate, terraform.tfvars, .env, id_ed25519
$ grep '^DOMAIN=' install-<name>/.env
$ terraform -chdir=deploy/terraform/envs/demo output -state=$PWD/install-<name>/terraform.tfstate
```

Write down the domain, the VM's IPv4 address and the instance id it prints. In the Vultr console (Instances) confirm an instance with **that IP and the label `<name>`** exists. If the label or IP differ from what you expect, stop: you may have the wrong folder or the wrong Vultr account.

## 1. Decide about the data (the owner decides, not the budget)

- **Customer data (real businesses):** keep the last backup until the customer confirms **in writing** that they do not need it. If the customer cannot be reached or credits run out, the owner of this project decides, in writing, and the default is to keep the bucket and the restic password (Object Storage costs about $18 a month: ask for more credit rather than deleting someone's data). Export anything else the customer asked for.
- **Synthetic demo data:** nothing to keep; skip to step 3.

## 2. Final backup (customer data only)

The VM must still run and SSH must be open ([access.md](access.md): `access.sh open --name <name>`). If the VM is gone or unreachable, the bucket already holds the backups made so far (check from any computer with the restic password and bucket keys: `restic snapshots`); the owner may waive this step **in writing** and the waiver goes in your log.

```bash
$ ssh -i install-<name>/id_ed25519 smb@<ip>
vm$ cd /opt/smb
vm$ docker compose stop caddy api worker          # nobody can write; the databases stay up for the backup
vm$ docker compose exec backup backup.sh --kind final
vm$ docker compose exec backup backup.sh --status
vm$ ./restore.sh --drill latest                    # proves the final snapshot can be restored (see backup-restore.md pass criteria)
vm$ docker compose exec postgres psql -U postgres -c 'drop database smb_drill' -c 'drop database keycloak_drill'
```

`--status` must say `"result": "ok"` with a `last_success` from the last minute, and the drill must pass, before you continue. Note the snapshot id (`docker compose exec backup restic snapshots`) in your log. Then **store the retention material in an encrypted place** (password manager attachment or encrypted drive): the **restic password**, the repository address and bucket keys from `install-<name>/.env`. Without them the retained bucket is useless. Date and record in your log: the date, the version (`cat /opt/smb/VERSION`), and why.

## 3. Destroy the VM and firewall

```bash
$ read -rs VULTR_API_KEY && export TF_VAR_vultr_api_key="$VULTR_API_KEY"
$ terraform -chdir=deploy/terraform/envs/demo init
$ terraform -chdir=deploy/terraform/envs/demo destroy \
    -state=$PWD/install-<name>/terraform.tfstate -var-file=$PWD/install-<name>/terraform.tfvars
```

(The state is a local file, which is how the installer created it; there is no remote backend or workspace.) Read the plan. Approve (type `yes`) only if **every** action is a destroy and the resources are a subset of: one `vultr_instance` (its `label` is `<name>`), one `vultr_firewall_group`, its `vultr_firewall_rule` entries (four for ports 80 and 443 over IPv4 and IPv6, plus any SSH rules), and one `vultr_ssh_key` named `<name>-install`. A subset is normal after a partial install or a retry. Anything else, or a different name: stop and escalate. If it fails part-way, run the same command again; Terraform continues with what is left. After success, the same command run again as `plan` (replace `destroy` with `plan`) must show nothing to destroy.

**Without Terraform state** (or if destroy keeps failing): in the Vultr console delete, in this order, the **instance** (match the **IP address and instance id** from step 0; if you have none, the label `<name>` and a creation time that matches your install), then the **firewall group** (description starts with `<name>:`), then the SSH key `<name>-install`. Check you are in the right account before each delete and record the ids you deleted in your log.

## 4. Remove what Terraform does not manage (Vultr console)

For each item **first check it belongs only to this installation** (look at what else is in it):

| Resource | Remove when |
|---|---|
| Object Storage **bucket** (for example `smb-backups`) | Step 1 allows it. Delete only the bucket(s) of this installation. |
| Object Storage **subscription** | It holds no other bucket you still need and step 1 allows it. The subscription bills monthly even when empty. |
| Container Registry | No other installation pulls from it |
| DNS records for `<domain>` (A and AAAA) | Always, at your DNS provider (a dangling record can be taken over by someone else) |

## 5. Delete the credentials

Only now: Vultr API key (Account → API; delete the one you used), registry credentials, and Object Storage keys that were only for this installation. Keep a credential if a remaining task (for example a retained bucket you still read) needs it.

## 6. Check the bill

Vultr console → Billing: "Current charges" shows what has accrued so far (normal) and must not keep growing after today for this installation. Instances: none for `<name>`. Products → Object Storage / Container Registry: only what you decided to keep in step 1. Check again tomorrow and after a week; anything still running or still growing is a mistake to fix at once.

## 7. Clean up your computer

`install-<name>/` holds secrets (`.env`, SSH key, Terraform state). If you **kept the backup bucket**, first copy `install-<name>/.env` to the encrypted place from step 2 (the restic password and bucket keys live there). Then move `install-<name>/` to encrypted offline storage or delete it. Deleting the state is safe only after step 3 finished (the empty plan above, or the console deletion) and step 6 shows nothing left.
