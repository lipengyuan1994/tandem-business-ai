# Security review, local half (task P6-06), 2026-11-03

Update: the same pass was re-run on the current build on 2026-11-03, with fixes and open items: [2026-11-03-rescan.md](2026-11-03-rescan.md).

**Scope and honesty.** This is the part of the P6-06 security pass that can run before anything is deployed: the production images and `compose.prod.yml` on one machine (`make rehearse --keep`), reached at `https://localhost:8443`. The roadmap's full pass also needs the public demo URL, a scan from another host over IPv4 and IPv6, and a person's review; those are **not done** and stay open for P6-06 after deployment. Nothing here is an external audit.

| Check | Tool | Result |
|---|---|---|
| Python dependencies | `pip-audit` on the locked production set (116 packages) | No known vulnerabilities |
| JavaScript dependencies | `pnpm audit --prod` | No known vulnerabilities |
| Container images | `trivy image` (critical and high, fixable) | api 0, backup 0, web 6 HIGH, all in the upstream Caddy 2.11.7 binary (Go standard library; Caddy 2.11.7 is the newest release) and Alpine `brotli-libs`: **accepted, re-scan before deployment** |
| Web scan (passive baseline) | OWASP ZAP 2.17.0 `zap-baseline.py` (spider, passive rules), unauthenticated, through Caddy | 0 high. 1 medium (accepted below), informational notes only |
| Egress | from the api container through `platform.http` to `https://example.org`, `http://169.254.169.254/...`, `http://postgres:5432`, `http://keycloak:8080/` | All four refused (not on the allowlist, or not https) |
| Published ports | `smoke.sh` and `docker ps` | Only Caddy publishes ports (80 and 443 mapped); Postgres, Keycloak, api, worker, backup do not |
| Keycloak admin | `GET /auth/admin/` and `/auth/admin/master/console/` through Caddy | 404 |
| Cookies | code and ZAP | Session and visitor cookies are `HttpOnly; Secure; SameSite=Lax`; the visitor cookie is scoped to its business's `/api/v1/public/<slug>/` path |
| Headers | ZAP and `smoke.sh` | HSTS, nosniff, referrer policy, permissions policy, CSP on app and api, cross-origin opener/embedder/resource policies on app pages, resource policy on assets |

## Changes made because of the scan
- The ZAP run first failed with a TLS error because its client sends no server name for `localhost`; Caddy now answers such clients with the site certificate (`default_sni`). No security effect.
- Added `Cross-Origin-Opener-Policy`, `Cross-Origin-Embedder-Policy`, `Cross-Origin-Resource-Policy` to app pages and `Cross-Origin-Resource-Policy` to the API and static assets (they were reported missing). The end-to-end suite still passes with them.

## Accepted, in writing
| Finding | Why accepted | Revisit |
|---|---|---|
| CSP `style-src 'unsafe-inline'` (medium) | The UI library injects a small `<style>` element at runtime and sets positioning through style attributes. Scripts stay `script-src 'self'` with no inline scripts, which is what blocks injected-code attacks; inline styles can only restyle. | Move to style nonces when the toast and popover libraries support them |
| Caddy 2.11.7 Go standard-library CVEs (trivy) | No newer Caddy release exists on the review date; we run a stock Caddy build without plugins; we have not analysed which of these CVEs are reachable through our configuration. | Re-scan at deployment and at every release |
| Egress control is in the application, not in the network | `platform.http` refuses disallowed destinations, but a compromised container could still open raw sockets: Docker's default network allows outbound traffic. The VM firewall group only filters inbound traffic. | Add host-level egress rules (nftables) in P8 hardening |
| The `smb` operator user can run Docker | Equivalent to root on the VM; documented in `docs/runbooks/access.md`. | P8: scoped operator access |

## Not covered (still open)
Authenticated ZAP scan and active scan; external port scan of IPv4 and IPv6; review of the first real deployment's headers and cookies behind Let's Encrypt; ASVS level 2 walk-through; penetration test; secrets rotation drill (`rotate-secrets.md` was read, not performed).
