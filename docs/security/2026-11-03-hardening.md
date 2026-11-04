# Whole-codebase hardening, 2026-11-03 (local, before kickoff)

Scope: everything built so far (platform, all business modules, frontend, deployment). AI features do not exist yet and were not in scope. Nothing was deployed; every result below is from the local machine.

## Scans

| Check | Tool | Result |
|---|---|---|
| Python production dependencies (113 locked packages) | `pip-audit` | No known vulnerabilities |
| JavaScript production dependencies | `pnpm audit --prod` | No known vulnerabilities |
| api and backup images | `trivy image` (high, critical, fixable) | 0 |
| web image | `trivy image` | Before: 3 Alpine (brotli-libs, curl, libcurl) + 6 in the Caddy binary. After: Alpine 0 (`apk upgrade` at build, curl removed); the 6 Caddy findings remain **accepted**: Caddy 2.11.7 (2026-10-03) is still the newest release (GitHub API, checked 2026-11-03). Re-scan at deployment. |

## Reviews

Four parallel independent reviews by Codex (GPT-6.1 Sol), each on one area across all modules: access control and tenant isolation; data integrity and concurrency; the public and untrusted-input surface; deployment and operations. They reported 40 findings (a few reported twice by two areas). Each finding was re-verified by the agent that fixed it; all were confirmed and fixed with regression tests that fail without the fix (three fix batches: identity and platform kernel; business modules; deployment). Highlights:

- Login: the OIDC callback is bound to the browser that started the login; the presented session is revoked after login; the absolute session timeout survives business switching in every profile.
- Owners: reactivating an owner needs owner rights; removing the last active owner is serialized.
- Idempotency: first use of a key is serialized; campaign execution re-checks the approved revision and audience.
- Concurrency: one lock order for stock across modules, campaign editing and conversations aligned with the approval engine; event handlers fenced against expired-lease replay; older delivery events cannot overwrite newer state.
- Correctness: DST fold kept when rounding slot boundaries; sales document dates and numbering years in the business time zone; catalog prices converted with the business currency; reorder crossings measured before reservations change.
- Public surface: request bodies on public routes capped at 128 KB in Caddy; rate-limit counts survive failed requests; visitor history bounded; the public-chat switch honoured on every visitor write path; visitor tokens revoked on erasure; audit CSV cells neutralised against spreadsheet formulas; database exception parameters no longer logged; Uvicorn access logs (which contained query-string tokens) disabled.
- Operations: restore works when the original database is missing; safety backups wait for the backup lock; installations stay restartable after a rollback; restored Keycloak client secrets are reconciled; the installer shows the owner password before fallible follow-up steps; relative Terraform paths normalised.

## Found during verification (not by the reviews)

- The fix for the body limit used a Caddy directive that does not exist (`buffer_requests`); the sandboxed agent could not run Caddy. `caddy validate` caught it; replaced with `request_buffers`.
- Live tests against a real Caddy 2.11.7 showed the first claim ("oversize bodies never reach the API") was false. What holds and is now tested: an oversize request with a declared length is rejected before the API is contacted; an oversize chunked request delivers at most 128,000 bytes and never a complete body. Comments and docs were corrected.
- The deploy regression tests were not part of `make check`; they are now (`deploy/tests`, live Caddy tests run when `caddy` is installed).

## Verification

`make check` (483 backend, 180 frontend, 17 deploy tests, licences, secret scan), `make e2e` on the dev stack (19), `make rehearse` (production images, 19 e2e, upgrade, rollback, restore; 251 s).

## Not covered

No external scan of a public URL, no load test on a VM, no penetration test, no review of AI features (not built), no real SMTP provider or BAA-covered setup.
