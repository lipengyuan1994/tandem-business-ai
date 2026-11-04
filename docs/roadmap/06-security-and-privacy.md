# 06 · Security, identity and privacy

## Threat model in one table

| Threat | Main control | Verified by |
|---|---|---|
| A user sees another business's data | Explicit `business_id` filters + row-level security + composite FKs | Cross-business tests in every module; `test_rls.py` |
| A staff member does something their role forbids | Permission check in the endpoint (`require`) **and** in the service (`ctx.require`) | Permission tests per endpoint (allowed and denied roles) |
| Prompt injection makes the AI act | AI has no mutating tools; proposals need human approval; tools run with the requesting user's permissions; untrusted text is fenced | Injection cases in the eval set; executor tests |
| Approved proposal is changed after approval | Approval binds `revision_id` + `payload_hash`; executor recomputes the hash | Approvals engine tests |
| Duplicate execution or messages | Atomic execution claim + idempotency keys + outbox states | Concurrency tests |
| Session theft / CSRF | HttpOnly Secure cookies, SameSite=Lax, CSRF token on unsafe methods, session rotation on login, idle and absolute timeouts | Auth tests; Playwright |
| Brute-force login | Keycloak brute-force detection + rate limit on `/auth/*` | Keycloak realm config test |
| Server-side request forgery / data exfiltration | Outbound HTTP only through `platform.http` with a host allowlist; model text never chooses URLs | Unit tests; egress test in P6 |
| Secrets leak | `.env` only, never in code, logs, prompts or the browser; secret scanning in `make check` (`gitleaks` when installed) | CI |
| Exposed database or admin console | Only Caddy publishes ports; Keycloak admin path blocked at Caddy; Vultr firewall group allows 80/443 (+22 from admin IP) | External port scan in P4-09 |
| Data loss | Encrypted off-site backups + restore tests | `make backup-test`; quarterly drill |

## Authentication flow (Keycloak, backend-for-frontend)

```mermaid
sequenceDiagram
  participant B as Browser (SPA)
  participant A as api /api/v1/auth
  participant K as Keycloak /auth
  B->>A: GET /auth/login?return_to=/today
  A->>A: create state, nonce, PKCE verifier (stored server-side, 10 min)
  A-->>B: 302 to K authorize endpoint
  B->>K: login form (+ TOTP)
  K-->>B: 302 /api/v1/auth/callback?code&state
  B->>A: GET /auth/callback
  A->>K: token exchange (code + verifier, client secret)
  A->>A: validate ID token (iss, aud, exp, nonce, signature via JWKS)
  A->>A: upsert users by oidc_subject; create sessions row; rotate any old session
  A-->>B: Set-Cookie smb_session (HttpOnly, Secure, SameSite=Lax, Path=/); 302 return_to
  B->>A: GET /auth/me → user, businesses, active business, permissions, csrf_token
```

- **Session record:** `sessions(id, token_hash, user_id, active_business_id, csrf_token_hash, created_at, authenticated_at, last_seen_at, expires_at, revoked_at, ip_hash, user_agent)`. The cookie holds a random 256-bit token, and only its SHA-256 hash is stored.
- **Timeouts:** standard idle 60 minutes, absolute 12 hours (configurable up to 24 hours). Regulated idle is 15 minutes and absolute is 8 hours from `authenticated_at`, which persists through business switches/session rotation. Reauthentication starts a new authentication lifetime.
- **CSRF:** `/auth/me` returns a per-session CSRF token. The SPA sends it as `X-CSRF-Token` on every unsafe method, and the server compares hashes in constant time.
- **Logout:** `POST /auth/logout` revokes the session and redirects to Keycloak end-session.
- **MFA:** the realm requires TOTP for all users (required action `CONFIGURE_TOTP`). WebAuthn passkeys can be enabled later.
- **Revocation:** suspending a membership or removing a role takes effect on the next request, because permissions are resolved per request from `role_grants`. Logging out everywhere revokes all of a user's sessions.
- **Bootstrap:** `python -m app.cli bootstrap-business --name … --slug … --timezone … --owner-email …` creates the business, creates the owner's Keycloak user through the admin API (temporary password and required actions) and creates an active owner membership. `install.sh` calls it.
- **Demo logins:** `python -m app.cli demo-logins --out FILE` creates Keycloak accounts for the staff of the synthetic demo businesses (Morgan, Riley and the others of `data/fixtures.json`) on a demo install, because the seed creates them as claimable users and nothing else creates their accounts. It is the one account-creating command that is not limited to dev, so it is fenced: only people listed in the fixtures (example.com) of businesses matched by slug and seed id, nothing for any other business or person, refused under `DATA_PROFILE=regulated`; random passwords go only to a mode-600 file, never to output or logs; the authenticator app (`CONFIGURE_TOTP`) stays mandatory, only the forced password change is off (`--temporary-password` turns it on). See [M01](modules/M01-identity.md).
- **Invitations:** an owner or admin invites by email and roles. The app creates a Keycloak user with required actions. If SMTP is configured, Keycloak emails the link. Otherwise the inviter sees a one-time temporary password once.
- **Visitors** (public chat and appointment requests) never log in. Starting a public conversation issues a random `visitor_token`. It is stored hashed in `visitor_tokens` and set as a cookie scoped to `/api/v1/public/{slug}/`. It grants access to that one conversation only and expires after 7 days. **Conversation IDs alone never grant access.**

## Permission model

Permissions are strings `<module>.<resource>.<action>`. Each module declares its own in `permissions.py`. Roles are fixed bundles of permissions defined in code (`identity/roles.py`) and versioned as `POLICY_VERSION`. Businesses cannot define custom roles in v1.

### Permission matrix

`R` = read, `W` = create/update, `A` = approve proposals of this type, `X` = admin action. A blank cell means no access.

| Area (permission prefix) | owner | admin | operator | inventory_manager | marketing_manager | auditor |
|---|---|---|---|---|---|---|
| Business settings, locations, hours (`org.*`) | R W X | R W | R | R | R | R |
| Staff and roles (`identity.membership.*`) | R W X | R W | | | | R |
| Customers, notes, consent (`crm.*`) | R W | R W | R W | R | R | R |
| Conversations and replies (`conversations.*`) | R W | R W | R W | | R | R |
| Catalog (`catalog.*`) | R W | R W | R | R W | R | R |
| Scheduling and appointments (`scheduling.*`) | R W | R W | R W | R | | R |
| Stock view (`inventory.stock.read`) | R | R | R | R | | R |
| Stock adjustment (`inventory.stock.adjust`) | W | W | | W | | |
| Reorder rules (`inventory.reorder.*`) | R W | R W | R | R W | | R |
| Marketing (`marketing.*`) | R W | R W | | | R W | R |
| Approvals queue (`approvals.proposal.read`) | R | R | R | R | R | R |
| Approve `scheduling.*`, `crm.*`, `conversations.*` actions | A | A | A | | | |
| Approve `inventory.*` actions | A | A | | A | | |
| Approve `marketing.*` send actions | A | A | | | A | |
| Audit log (`audit.event.read`, `audit.export`) | R X | R | | | | R X |
| AI assistant use (`ai.assistant.use`) | ✓ | ✓ | ✓ | ✓ | ✓ | (read-only questions) |
| AI policy and budgets (`ai.settings.*`) | R W | R | | | | R |
| System, backups, erasure (`admin_ops.*`) | R X | R | | | | |

**Approval rule:** who may approve a proposal depends on its `action_type` (`approvals.policy.APPROVER_PERMISSION[action_type]`). By default the creator of a staff-originated proposal cannot approve it. A business setting `allow_self_approval` (default `false`; `true` for one-person businesses) changes that. Assistant-originated proposals can be approved by any staff member holding the approve permission, including the user the assistant was helping.

## Network exposure

- **Vultr firewall group** (Terraform): inbound TCP 80 and 443 from anywhere, TCP 22 only from `admin_cidr` (empty by default, in which case the Vultr web console is used). Rules cover IPv4 and IPv6.
- **Host:** only the Caddy container has a `ports:` mapping. Postgres, Keycloak, api and worker have none. `ufw` is enabled with default deny inbound, but Docker port publishing bypasses `ufw`, so the real control is publishing nothing else.
- **Caddy:** routes `/` to the SPA, `/api/*` to api and `/auth/*` to Keycloak, **except** `/auth/admin/*` and `/auth/realms/master*` (the administrator's own login), which return 404 publicly. Keycloak administration happens over an SSH tunnel. Caddy's error log masks capability tokens, OIDC `code`/`state` and unsubscribe tokens in the request URI and Referer (`deploy/tests/test_capability_logging.py`). Security headers: HSTS, a CSP with `default-src 'self'`, `X-Content-Type-Options`, `Referrer-Policy: same-origin`, `Permissions-Policy` (microphone only on voice pages).
- **Verification:** P4-09 runs an external scan (`nmap` from another host) against IPv4 and IPv6 and records the results.

## Egress

All outbound HTTP from the backend goes through `platform.http.client()`. It:
1. Refuses hosts not in `EGRESS_ALLOWLIST` (exact host match).
2. Refuses literal IPs, loopback, link-local, private ranges and the cloud metadata address `169.254.169.254`, even if DNS resolves an allowed name to one.
3. Sets timeouts (connect 5 s, read 30 s by default) and a maximum response size.

Strict-private mode removes every hosted inference host from the allowlist. In v1, host-level egress filtering (`DOCKER-USER` iptables chain) is added as defence in depth (P8).

## Secrets

| Secret | Created by | Stored |
|---|---|---|
| DB passwords, `APP_SECRET_KEY`, OIDC client secret, Keycloak admin client secret | `install.sh` (random) | `/opt/smb/.env` on the VM (mode 600) and the operator's password manager |
| `RESTIC_PASSWORD` | `install.sh` | VM `.env` + **printed once for the owner to store offline**. Without it, backups cannot be restored |
| Object Storage keys | Vultr console or API (owner) | VM `.env` |
| `VULTR_INFERENCE_API_KEY` and other model keys | Owner | VM `.env`, never sent to the browser or put in prompts |
| Customer's Vultr account API key (for Terraform) | Owner | Only on the operator's machine during install, **never on the VM** |

Rotation is documented in the runbook ([11](11-quality-and-operations.md#runbooks-written-in-p4p8-under-docsrunbooks)): rotate, restart containers, verify.

## Privacy

- **Data minimisation:** collect name, contact points, consent and service history only. No dates of birth, ID numbers or payment data.
- **Hosted AI disclosure:** each business has `ai_policy`:
  - `hosted_vultr`: Vultr Serverless Inference only. This is the default.
  - `hosted_any`: Vultr plus configured external providers.
  - `strict_private`: local model only. If none is configured, AI features are off. They never fall back to a hosted provider.

  The settings page states in plain words where text is processed. The Vultr catalog lists US data centres (`sea`, `atl`, `sjc`) as of 2026-11-03, which matters for EU customers.
- **Prompts and AI logs:** `ai_runs` stores token counts, latency, cost and tool names. It does **not** store prompts or completions by default. `ai_run_steps` stores tool arguments and short result summaries for the approval evidence, and these are redacted for contact details. A business can enable full prompt logging for debugging for 24 hours.
- **Audio:** never stored by default. Voice transcripts become normal messages ([M14](modules/M14-voice.md)).
- **Logs:** no message bodies, emails or phone numbers. IDs only.

## Retention and erasure

| Data | Default retention | Configurable |
|---|---|---|
| Customer master data | Until erased or archived + 3 years | Yes |
| Conversations and messages | 2 years | Yes |
| Audit events | 7 years | No shorter than 1 year |
| Sessions | 30 days after expiry | No |
| `ai_runs`, `ai_run_steps` | 90 days | Yes |
| Jobs and events (done) | 30 days | No |
| Backups | 7 daily, 4 weekly, 6 monthly | Yes |

**Erasure** (v1, M18): an owner files an erasure request for a customer. Each module registers an erasure handler with the platform registry, which replaces personal fields with a tombstone and keeps IDs and amounts. Audit events keep IDs, with summaries rewritten to "erased customer". Backups age out according to retention, and the UI states that honestly. **As built (2026-11-03):** erasure and export work as described in [M18](modules/M18-admin-ops.md#as-built-data-export-and-customer-erasure-2026-11-03); a suppression for the customer's address is kept so they are never mailed again; retention enforcement (the table above) is not built yet, so nothing is deleted automatically.

## Compliance statement

We do not claim GDPR, HIPAA, SOC 2 or PCI compliance. The design supports a customer's own compliance work: data stays in their account, plus audit, export, erasure and retention. A legal review is required before any real-data pilot (P9 gate).
