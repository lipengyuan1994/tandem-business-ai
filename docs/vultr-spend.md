# Vultr spend log

Hard limit: **$200 of credit for the whole project** (owner, 2026-11-03). Every resource that bills is listed here when it
is created and when it is destroyed. Prices are Vultr list prices; Vultr bills hourly up to the monthly cap.

Check the balance at any time: `GET https://api.vultr.com/v2/account` (`balance` is negative while credit remains;
`pending_charges` is this month's spend so far).

## Budget plan

| Item | Price | Plan |
|---|---|---|
| Serverless Inference | token-billed (subscription details in the Vultr console) | Probe, evals, demo traffic: expected well under $20 |
| Demo VM `vhp-2c-4gb-amd` | $24/month (≈ $0.036/hour) | Test deploy; keep for the demo only if the owner wants it running |
| Container Registry | Start Up plan: free (10 GB) | Images are about 0.7 GB per version |
| Object Storage | $18/month (≈ $0.025/hour) | Needed for backups; destroy after the backup and restore test unless the demo keeps running |

## Ledger

| UTC time | Action | Resource | Running cost | Balance after (credit left) |
|---|---|---|---|---|
| 2026-11-03 | Credit applied | — | — | $200.00, pending $0.00 (checked via API) |
| 2026-11-03 02:02 | Created | Container Registry `smbopsrqk49` (`sjc.vultrcr.com/smbopsrqk49`, private, Start Up plan) | $0.00/month | $200.00 |
