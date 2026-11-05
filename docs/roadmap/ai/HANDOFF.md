# AI build handoff (current state)

**Rules for this file.** It is the *only* memory between sessions. After each piece of work, **rewrite** it (do not
append a diary): keep it under 80 lines and true *now*. History lives in `EVENT_WORK.md` and git. The module card
`backend/app/modules/ai/README.md` lists every file, function and endpoint.

## State (2026-11-04 ~07:40 UTC)

- **All AI cards built** (AI-01 … AI-15) and reviewed; AI-09 selection and AI-14 live injection are measured below.
- **Live models** (`deploy/config/ai-routes.yaml`, Vultr only, `json_object` mode everywhere): agent, safety and
  fast_extract `mimo-v2.6-flash` (fallbacks `deepseek-v4.1-flash-1017` / `qwen3.8-flash-next`); writer
  `deepseek-v4.1-flash-1017`. Do not use `glm-5.3-flash` (reasoning model: empty output within 800 tokens) or
  `json_schema` mode (empty output for nested schemas on Vultr). Probe: `backend/evals/reports/2026-11-03-probe.md`.
- **Latest live evals** (2026-11-04, all six suites meet their thresholds): intake 92–95 % (62 cases, prompt v3),
  time zones 90 %, injection 100 % pass and 100 % resistance, copilot 80 %, summary 100 %, procurement 80 %. Selection
  report `backend/evals/reports/2026-11-03-selection.md`; run with `AI_LIVE=1 make eval suite=<suite>`.
- **Deployment:** Vultr VM `smb-demo` (45.63.85.83, `https://45-63-85-83.sslip.io`, vhp-2c-4gb-amd, sjc) runs **0.1.5**
  with live AI, watchdog timer, backups to Object Storage `smb-demo-backups`; registry `sjc.vultrcr.com/smbopsrqk49`.
  Install folder `install-smb-demo/` (gitignored: Terraform state, SSH key, `.env`). Spend log `docs/vultr-spend.md`.
- **Hardening done:** second-model AI review (16 findings fixed, `docs/security/2026-11-03-ai-second-review.md`) and
  operations review (`docs/security/2026-11-03-ops-readiness-review.md`; fixes: worker capacity, DB timeouts, retention
  cleanup, streamed exports, watchdog + webhook alerts, backup maintenance status, key-only SSH, time sync, hard AI
  budget with installation-wide USD ceilings, public-chat AI quota).
- Contract changes so far: `effect_key` includes `trigger_key`; copilot thread models `CopilotThreadOut`/
  `CopilotThreadDetailOut`; `Idempotency-Key` on copilot/summary/extract (§14); budget reservation rules (§12);
  `payment_followup` gets `sales.document.write` (owner decision); intake prompt v3 preloads context (≈ 5 steps);
  customer matching: an unmatched e-mail/phone outweighs a name-only overlap, a surname alone is no identity, and
  ambiguity no longer stops intake (2026-11-04).
- Tests (2026-11-04 07:20): `make check` 1,529 backend, 279 frontend, deploy checks, licences, no leaks; `make e2e` 30;
  `make rehearse` passed (05:00).

## Open items (owner)

- Alert destination: set `ALERT_WEBHOOK_URL` on the VM (`docs/runbooks/monitoring.md`) and add an external uptime check.
- Customer domain (sslip.io is demo-only), real SMTP provider, second backup copy under separate credentials, named
  operator for alerts, data-processing agreements (ops review items 1, 5, 13, 16, 19).
- SSH to the VM is closed (only 80/443); open it with `deploy/scripts/access.sh open --name smb-demo` and close it
  again after use. Terraform state matches the VM (2026-11-04 12:15).
- Signed-in capacity test on the VM passed 2026-11-04 (P6-07: p95 204 ms, 0 errors). To repeat it: demo logins in
  `install-smb-demo/demo-logins.txt` (gitignored); sign in with `pnpm exec playwright codegen --save-storage=…` from
  `frontend/`, then run `deploy/scripts/loadtest.py --business "Example Cycle Works"` from the repo root.
- Commits: the first commit exists; later work is uncommitted until the owner approves.

## Next up

1. Owner items above. P6-06, P6-07 and P6-08 are done (2026-11-04). Open hardening: P8-09 raw container egress
   (needs an owner decision on an egress proxy). Domain, README, slides and video come after the main features.
2. Recurring: re-run the six eval suites after any prompt or route change; keep the spend log current.

## Environment notes

- Test database: `localhost:5433/smb` (container `db-test`, in-memory: restarting the dev stack wipes extra
  databases such as `smb_eval`; re-create with `make test-db-new name=<n>` and migrate it with
  `DATABASE_MIGRATION_URL=… .venv/bin/alembic upgrade head`). Don't run two full suites at once (disk fills).
- Codex sandbox: `.venv/bin/python -m pytest -q -p no:cacheprovider`; no Docker; `make check`, `make gen-api`,
  `make e2e`, `make rehearse` and live evals are run by the supervisor (the inference key never goes to Codex).
