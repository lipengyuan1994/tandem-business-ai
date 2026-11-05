# Security pass on the public demo (task P6-06), 2026-11-04

**Target:** the Vultr demo VM `smb-demo`, `https://45-63-85-83.sslip.io` (IPv4 45.63.85.83, IPv6
2001:19f0:ac02:e4e:5400:7ff:fe00:fb95), release 0.1.4, Let's Encrypt certificate. Scans ran from the owner's home
connection (a laptop), against our own server only. This is an agent's scan, **not** an external audit or a penetration
test. Earlier local reviews: [2026-11-local-review.md](2026-11-local-review.md), [2026-11-03-rescan.md](2026-11-03-rescan.md).

## Result

**No high findings.** One medium (unchanged, accepted below) and one open hardening item (raw egress from containers,
planned as P8-09).

| Step (roadmap) | Tool and method | Result |
|---|---|---|
| ZAP baseline against the demo URL | OWASP ZAP stable (`ghcr.io/zaproxy/zaproxy:stable`), `zap-baseline.py`, spider 2 min, passive rules, unauthenticated, on `/` and `/p/example-cycle-works/chat` | Each **0 fail, 4 warn, 64 pass**. Warnings: CSP `style-src 'unsafe-inline'` (medium, accepted), Modern Web Application, Re-examine Cache-control Directives, Storable and Cacheable Content (informational; same as the local runs) |
| `pip-audit` | `pip-audit --strict --no-deps` on the locked production set exported from `uv.lock` (117 packages, local project excluded) | No known vulnerabilities |
| `pnpm audit` | `pnpm audit --prod` | No known vulnerabilities |
| External port scan, IPv4 | `nmap -Pn -sT -p-` (all 65,535 TCP ports) | Only **80 and 443** open; everything else filtered (no response) |
| External port scan, IPv6 | same, `-6` | Only **80 and 443** open |
| TLS | `nmap --script ssl-enum-ciphers -p 443` | TLS 1.2 and 1.3 only; every cipher grade A (ECDHE with AES-GCM or ChaCha20; TLS 1.3 with X25519MLKEM768); least strength A |
| Egress test | On the VM, from the api container through `platform.http.client()` | `https://example.org`, `http://169.254.169.254/…` (metadata), `http://postgres:5432`, `http://keycloak:8080`, `https://localhost` **refused**; `https://api.vultrinference.com` (on `EGRESS_ALLOWLIST`) allowed. A raw socket from the same container to `example.org:443` **connects** (see open items) |
| Headers | `curl -I` on the live site | HSTS `max-age=31536000`, CSP (`default-src 'self'; script-src 'self'; frame-ancestors 'none'; …`), `nosniff`, `referrer-policy: same-origin`, permissions policy, COOP/COEP/CORP; API responses `cache-control: no-store`; no `X-Powered-By` |
| Cookies | live login redirect and code | `__Host-smb_oidc` (`HttpOnly; Secure; SameSite=Lax; Path=/`, 10 min); staff `smb_session` `HttpOnly; Secure; SameSite=Lax`; visitor `smb_visitor` scoped to `/api/v1/public/<slug>/` |
| Exposed admin surfaces | `curl` on Keycloak admin and master-realm paths, API docs, dotfiles | `/auth/admin/`, `/auth/realms/master…` → **404**; `/api/docs`, `/api/openapi.json` → 404; `/.env`, `/.git/config`, `/metrics`, `/admin/`, `/realms/master` → the single-page app's fallback page (identical HTML for every unknown path; no file or service behind them) |

Also on 2026-11-04: SSH is closed in the Vultr firewall group (opened only for minutes at a time with
`deploy/scripts/access.sh`, for this review's egress test and the P6-08 restore drill). Root and `smb` log in with keys only.

## Accepted findings

| Finding | Why accepted | Revisit |
|---|---|---|
| CSP `style-src 'unsafe-inline'` (ZAP 10055, medium) | The UI library injects styles; scripts stay `script-src 'self'`, so the main XSS vector is closed | Style nonces when the toast and popover libraries support them |
| Unknown paths return the app page with HTTP 200 (soft 404) | Single-page app fallback in Caddy; no content leaks (checked above) | Optional: return 404 for dotfile paths in Caddy |
| `Via: 1.1 Caddy` header | Names the proxy, not a version | — |
| HSTS without `includeSubDomains`/`preload` | `sslip.io` is a shared demo domain; set both once the owner's own domain is in place | With the customer domain |

## Open items

1. **Raw egress from containers (P8-09).** The application refuses non-allowlisted destinations, but a compromised
   container could still open its own sockets: Docker's default network lets containers reach the internet, and the
   Vultr firewall group filters inbound traffic only. A real fix needs an egress allowlist by host name (an egress
   proxy) or host firewall rules in the `DOCKER-USER` chain; both need a design decision (a proxy is a new container,
   which needs the owner's approval under ADR rules).
2. **Not run:** UDP port scan (needs administrator rights on the scanning laptop); authenticated and active ZAP scans;
   a scan from a second network; ASVS level 2 walk-through; a person's review; a penetration test.
