# Production-readiness review of deployment and operations (GPT-6.1 Sol, read-only), 2026-11-03


**The deployment has a solid baseline, but I would not yet hand it over as an unattended paid service.** The biggest risks are silent failures, an incomplete fresh-VM recovery procedure, and cost/abuse controls that can exhaust AI availability.

This reviews the current working tree and repository records, not the VM’s live configuration. Your successful installation, Object Storage backups, Let’s Encrypt issuance, 0.1.1 upgrade, and roughly 1.6 GB memory usage are **user-reported deployment evidence**.

Already covered, so not findings: Docker logs rotate at 10 MB × 5 per container; every service has a memory limit; PostgreSQL has 256 MB shared buffers; backups are encrypted, off-VM, and have retention; restore drills and secret rotation are documented; Keycloak’s public realm has brute-force protection and mandatory TOTP; admin/master routes are blocked publicly; public forms already have rate/body limits; export and erasure exist.

Ranked by likely customer impact:

1. **Critical — Nobody is automatically told when service deteriorates.**  
   Files: [operations roadmap](docs/roadmap/11-quality-and-operations.md:75), [runbook handover](docs/runbooks/README.md).  
   The roadmap describes metrics and alerts, but I found no deployed collector or operational alert delivery. The responsible person is a blank handover field. Customers may discover outages, overdue backups, dead jobs, full disks, or expired certificates first. **Fix:** name primary/backup operators; configure external HTTPS/API/login-discovery checks and an independently delivered pager/email channel; report disk/inodes, OOMs, job age, backup age, certificate expiry, and AI failures. Force every alert once and record receipt.

2. **Critical — Fresh-VM disaster recovery is not an executable, proven procedure.**  
   Files: [new-VM recovery](docs/runbooks/backup-restore.md:85), [installer](deploy/scripts/install.sh).  
   The runbook says to stop the installer before bootstrap; the script has no matching pause/restore mode. It also starts backups against fresh databases. Database snapshots omit deployment configuration and secrets. **Fix:** add a restore mode that suppresses bootstrap and scheduled backups, pins the chosen snapshot, and consumes an encrypted recovery bundle containing configuration, secrets, and image versions. Prove recovery from Object Storage onto a replacement VM using offline-held credentials. Keep the existing quarterly cadence, assign its owner, record RTO, and obtain customer acceptance of approximately six-hour RPO—or implement PITR.

3. **High — The AI token budget is not a hard financial ceiling.**  
   Files: [budget checks](backend/app/modules/ai/usage.py), [model caller](backend/app/modules/ai/agent/runner.py:105), [route prices](deploy/config/ai-routes.yaml).  
   Calls check previously recorded usage without reserving allowance; concurrent calls can overshoot. Failed/timed-out billable attempts may not enter successful-call accounting. A stolen provider key bypasses application controls. At checked-in prices, two million tokens represents roughly **$0.40–$1.20/day/business**, before overshoot. **Fix:** atomically reserve a conservative per-call allowance, reconcile usage, add installation-wide dollar/day/month limits and concurrency limits, reconcile provider billing, and use provider-side restrictions where available. The existing 80% notice is in-app; add independent 80%/100% notifications.

4. **High — Public abuse can consume the customer’s AI allowance and database capacity.**  
   Files: [chat endpoints](backend/app/modules/conversations/api.py:156), [quote endpoints](backend/app/modules/purchasing/api_public_quotes.py), [Caddyfile](deploy/compose/Caddyfile).  
   Existing IP/conversation/invitation limits help, but distributed clients can exhaust a business’s allowance. Rejected requests still reach application/database rate accounting; there is no configured edge throttle. Direct Keycloak requests also bypass the application’s login limiter. **Fix:** add edge burst/concurrency limits, per-business public-intake quotas, bounded pending AI work, and escalation challenges for suspicious creation traffic. Preserve staff AI allowance separately. Verify forwarded-IP handling with spoofed headers and shared-office clients. Signed quote capabilities already prevent arbitrary unauthenticated submissions.

5. **High — Backups share too much of the production failure boundary.**  
   Files: [backup credentials and maintenance](deploy/scripts/backup.sh), [infrastructure topology](docs/roadmap/10-vultr-infrastructure.md).  
   The VM has credentials capable of pruning its backup repository. Host compromise can therefore destroy live data and backups; a Vultr account/storage incident is another shared risk. **Fix:** maintain a separately controlled recovery copy, preferably another account/provider or failure region, using independently held credentials. Separate routine backup writes from destructive maintenance where supported, and test recovery from that copy. I cannot establish whether the live bucket already has additional protection.

6. **High — Memory limits exist, but their combined worst case leaves little host reserve.**  
   Files: [production Compose](deploy/compose/compose.prod.yml), [export implementation](backend/app/platform/export.py).  
   Long-running service limits total **3,456 MiB**, rising to **3,712 MiB** with migration, before the OS and Docker. The reported 1.6 GB usage does not establish peak safety. Exports accumulate complete datasets in memory; restic maintenance can also peak. **Fix:** measure simultaneous load/backup/export/upgrade peaks, reserve explicit host headroom, define swap behaviour, and alert on OOM/restart counts. Stream large exports. Resize to 8 GB if measured headroom is insufficient. Docker restarts exited containers; an `unhealthy` state alone does not trigger restart.

7. **High — A healthy worker can still stop making useful progress.**  
   Files: [worker](backend/app/worker.py:33), [job queue](backend/app/platform/jobs.py).  
   Heartbeat comes from housekeeping, not successful job completion. Four slow AI jobs can occupy every execution thread; event delivery runs only when no job was claimed. Compose also lacks a shutdown grace period matching potentially 90-second AI runs. **Fix:** reserve execution capacity for approvals/mail/events, provide fair event scheduling, monitor oldest due work and completion progress, and add a tested drain period. Exercise shutdown mid-AI-call and mid-mail-delivery.

8. **High — PostgreSQL’s connection and maintenance budgets need coordination.**  
   Files: [PostgreSQL settings](deploy/compose/compose.prod.yml:58), [application pools](backend/app/platform/db.py:28).  
   PostgreSQL is capped at 1 GB with 100 connections. Each application process permits 20 connections, while Keycloak’s pool is not explicitly sized. Bursts can exhaust connections or memory. Autovacuum is **not disabled**, but default thresholds may lag on growing, frequently updated queue/session tables. **Fix:** coordinate application and Keycloak pool limits, reserve administrative connections, bound idle transactions and ordinary query time, and monitor dead tuples, vacuum age, locks, and transaction-ID age. Tune hot tables from measurements rather than routinely running `VACUUM FULL`.

9. **High — Log rotation does not bound total disk growth.**  
   Files: [retention specification](docs/roadmap/06-security-and-privacy.md), [disk incident procedure](docs/runbooks/incident.md).  
   Completed jobs/events, expired sessions, rate-limit windows, and other records lack general automatic cleanup. Old images, restic cache, scratch/previous databases, host journals, and plaintext temporary dumps also consume disk. PostgreSQL and backups can fail together when it fills. **Fix:** add bounded, indexed cleanup jobs preserving deduplication/audit requirements; set host-journal limits; establish image/cache/restore-database cleanup procedures; alert on free bytes, growth rate, and inodes. Keep explicit spare space for backup and restore.

10. **High — Backup health misses maintenance failures and detects missed backups late.**  
    Files: [backup health and maintenance](deploy/scripts/backup.sh:15), [schedule](deploy/scripts/backup.crontab).  
    “Healthy” tolerates 26 hours without success despite a six-hour schedule. Prune/integrity-check failures do not update the backup status JSON. Repeated random 5% checks do not guarantee complete coverage. **Fix:** alert on each failed attempt and backup age over seven hours; publish separate maintenance/check success timestamps; verify retained snapshot dates/counts; use rotating integrity subsets plus periodic full verification. Existing retention rules need verification, not replacement.

11. **High — Upgrade downtime is only lightly bounded.**  
    Files: [upgrade script](deploy/scripts/upgrade.sh:103), [upgrade runbook](docs/runbooks/upgrade.md).  
    Migration runs while the old application is serving traffic; schema locks can stall it. There is no overlapping API deployment, and deployment files are staged manually. Image rollback retains the new Compose configuration; PostgreSQL/Keycloak upgrades have separate compatibility risks. **Fix:** document a maintenance window, drain writes/jobs where necessary, use migration lock/statement timeouts, retain versioned configuration bundles and image digests, and test rollback/reboot with the preceding release. The successful 0.1.0→0.1.1 upgrade verifies that path, not future migration downtime.

12. **High — Security updates lack an ongoing owner and tested maintenance cycle.**  
    Files: [cloud-init](deploy/terraform/modules/vultr-single-vm/user-data.yaml.tftpl), [image/update procedure](docs/runbooks/upgrade.md).  
    Ubuntu unattended upgrades are enabled. That does not establish Docker-repository update coverage, kernel reboot completion, or updates to digest-pinned container contents. **Fix:** assign monthly patch/rebuild/re-scan windows and an expedited critical-fix process; verify allowed apt origins, failed updates, and reboot-required status; test Docker/Compose upgrades and VM reboot recovery. Preserve explicit PostgreSQL/Keycloak upgrade procedures. Actual VM patch/reboot state is unknown.

13. **High — SMTP configuration does not establish deliverability.**  
    File: [email runbook](docs/runbooks/email.md).  
    Transport security and separate Keycloak testing are documented, but SPF/DKIM/DMARC, bounce/complaint processing, reputation, and inbox-placement checks are not operationalized. SMTP acceptance can still mean lost invoices or password resets. Empty SMTP intentionally simulates normal mail. **Fix:** make real-mail readiness a customer handover gate: verified sender domain, authentication/alignment checks, independent receipt tests for application mail/invitations/resets, and provider bounce/complaint handling with suppression and operator notification. Live delivery remains unverified.

14. **High — There is no defensible capacity promise for this VM.**  
    Files: [recorded benchmark](docs/performance-local.md), [load generator](deploy/scripts/loadtest.py).  
    The recorded result—20 simulated staff, 19.5 requests/sec, p95 26 ms—is from the Mac with tiny data and reads only. It establishes no 4 GB VM limit for staff, messages/day, or AI runs/day. **Fix:** run a representative mixed workload on that plan: realistic data growth, staff reads/writes, visitor polling/messages, AI, mail, backups, and exports. Publish a conservative supported envelope and resize triggers based on latency, backlog, memory, and disk.

15. **High — Secret custody and rotation are procedures without recorded acceptance evidence.**  
    Files: [rotation runbook](docs/runbooks/rotate-secrets.md), [offline recovery requirements](docs/runbooks/backup-restore.md:9).  
    Offline restic custody and encrypted `.env` copies are already required. What remains unknown is whether another authorized person can retrieve them, whether saved copies stay current, and whether rotations—including historical-snapshot restoration—work. Losing the password makes backups unusable. **Fix:** maintain a credential inventory with custodian/expiry dates; verify recovery using escrowed credentials; rehearse DB, OIDC, storage, SMTP, and inference-key rotation and revoke old credentials. Record the actual Keycloak admin credential separately from bootstrap values.

16. **High — Data location and contractual obligations need customer-specific approval.**  
    Files: [privacy/security policy](docs/roadmap/06-security-and-privacy.md), [regulated-profile limits](docs/runbooks/regulated-profile.md).  
    Choosing a VM region does not establish inference, backup, SMTP, or support-access location. The documentation identifies US inference locations, but does not establish binding processing/retention terms for a particular customer. **Fix:** document the complete data flow and subprocessors; approve processing agreements, transfer mechanisms, privacy notices, breach responsibilities, and retention schedules before real data. For regulated customers, verify actual agreement coverage; configuration attestations are not evidence of coverage.

17. **High — Retention and post-restore erasure obligations remain incomplete.**  
    Files: [retention commitments](docs/roadmap/06-security-and-privacy.md), [restore procedure](deploy/scripts/restore.sh).  
    Export/erasure and 90-day copilot-message cleanup exist, but general retention enforcement does not. Restoring an older snapshot can resurrect erased data, mailing eligibility, or revoked access. **Fix:** implement approved retention schedules and retain a protected deletion/restriction/revocation record outside the restored snapshot’s timeline. Reapply it before reopening traffic or mail. Test an erase→backup restore scenario and document backup-expiry exceptions honestly.

18. **Medium — SSH and Keycloak administration need a verified handover baseline.**  
    Files: [cloud-init](deploy/terraform/modules/vultr-single-vm/user-data.yaml.tftpl), [admin access](docs/runbooks/access.md).  
    Provider SSH CIDR rules and tunnel-only administration are implemented. However, host UFW permits SSH generally, sshd authentication policy is not explicitly set, and master-admin MFA is a manual step. **Fix:** explicitly configure key-only SSH and network root-login policy, verify master-admin MFA and break-glass access, close temporary SSH rules, and scan IPv4/IPv6 after provisioning. I cannot infer current sshd settings or exposure from the template.

19. **Medium — The working sslip.io address must be replaced for a customer.**  
    Files: [domain guidance](docs/runbooks/install.md), [TLS configuration](deploy/compose/Caddyfile).  
    The documentation correctly restricts sslip.io to demos. It depends on third-party DNS and embeds an IP that may change during recovery. **Fix:** use a customer-controlled domain; document A/AAAA and Keycloak redirect changes, registrar/DNS ownership, and renewal contacts. Check certificate expiry externally and test renewal-failure alerting. Successful initial issuance does not verify renewal.

20. **Medium — Time synchronization is assumed, not checked.**  
    File: [cloud-init](deploy/terraform/modules/vultr-single-vm/user-data.yaml.tftpl).  
    Clock drift affects TOTP, OIDC validity, sessions, job leases, schedules, and backup-age measurements. Ubuntu may already provide working synchronization; the repository neither verifies nor monitors it. **Fix:** explicitly enable and verify the existing time-sync service, record synchronized status during installation/reboot checks, and alert on persistent loss of synchronization or excessive offset.

No files or infrastructure were changed. **`make check` and runtime/failure-injection tests were not run**: the full check starts/writes test databases and produces artifacts, outside this read-only review. Findings above are source-backed; live configuration, alert receipt, renewal, production mail, peak capacity, and fresh-VM recovery remain untested here.