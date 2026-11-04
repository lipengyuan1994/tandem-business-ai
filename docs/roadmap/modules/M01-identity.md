# M01 · identity

| Field | Value |
|---|---|
| Release / phase | MVP · P2 |
| Tier / kind | **A** · PRE |
| Depends on | platform, org |
| Owns tables | `users`, `sessions`, `memberships`, `role_grants`, `invitations`, `oidc_login_states` |

## Purpose
Authenticates staff through Keycloak (backend-for-frontend OIDC), manages server-side sessions, business memberships, role grants and invitations, and resolves a request's effective permissions. Defines the role → permission bundles. Does **not** store passwords or MFA secrets; Keycloak does.

## Tables
`users` — a person who can log in (global, no `business_id`, no row-level security).

| Column | Type | Null | Notes |
|---|---|---|---|
| id | uuid | no | |
| oidc_subject | text | no | unique; Keycloak `sub` |
| email | text | no | **PII**; unique (lower-cased) |
| display_name | text | no | **PII** |
| status | text | no | `active` \| `disabled` |
| last_login_at | timestamptz | yes | |

`sessions` — a browser session (global).

| Column | Type | Null | Notes |
|---|---|---|---|
| id | uuid | no | |
| token_hash | char(64) | no | unique; SHA-256 of the cookie token |
| user_id | uuid | no | FK users |
| active_business_id | uuid | yes | FK businesses |
| csrf_token_hash | char(64) | no | |
| created_at, last_seen_at, expires_at | timestamptz | no | absolute expiry |
| revoked_at | timestamptz | yes | |
| ip_hash, user_agent | text | yes | IP stored as a salted hash |

`memberships` — a user's membership in one business. Columns: `user_id` (FK), `status` (`invited` \| `active` \| `suspended` \| `removed`), `title` (e.g. "Technician"). Unique `(business_id, user_id)`.

`role_grants` — roles held through a membership. Columns: `membership_id` (composite FK), `role` (`owner` \| `admin` \| `operator` \| `inventory_manager` \| `marketing_manager` \| `auditor`), `granted_by`, `granted_at`, `revoked_at` (null = active). Unique active `(membership_id, role)` (partial unique index where `revoked_at is null`).

`oidc_login_states` — global, short-lived: `state_hash`, `nonce`, `code_verifier`, `return_to`, `expires_at` (10 minutes); deleted on use or by the nightly cleanup.

`invitations` — columns: `email` (**PII**), `roles text[]`, `invited_by`, `keycloak_user_id`, `expires_at`, `accepted_at`, `status` (`pending` \| `accepted` \| `revoked` \| `expired`).

## Business rules
1. The login callback validates `state`, `nonce`, PKCE and ID token signature, issuer, audience and expiry. Any failure → 400 `identity.login_failed` (no detail leaked).
2. The first login with an email that matches a pending invitation activates that membership and its roles.
3. A business always has at least one active `owner`. Removing or suspending the last owner → 409 `identity.last_owner`.
4. Only an `owner` can grant or revoke `owner`. `admin` can grant every other role.
5. Effective permissions = union of the active roles' bundles (`roles.py`, `POLICY_VERSION`), recomputed on **every request**. Revocation is immediate.
6. Session cookie token: 32 random bytes, base64url. Rotated on login and on business switch. Idle and absolute timeouts per [06](../06-security-and-privacy.md#authentication-flow-keycloak-backend-for-frontend).
7. Logging out revokes the session. "Log out everywhere" revokes all of the user's sessions.
8. A suspended or removed membership cannot become the active business.
9. Identity registers the session resolver with platform at startup (`reg.set_session_resolver(...)`).

## Public interface (`public.py`)
```python
def get_user_brief(db, ctx, user_id: UUID) -> UserBrief                     # id, display_name (for audit/UI)
def effective_permissions(db, user_id: UUID, business_id: UUID) -> frozenset[str]
def has_permission_now(db, user_id: UUID, business_id: UUID, permission: str) -> bool   # approvals uses this
def list_users_with_permission(db, ctx, permission: str) -> list[UserBrief]  # notifications: who can approve
def bootstrap_business(db, *, name, slug, timezone, currency, owner_email, owner_name) -> BootstrapResult  # CLI only
ROLES: dict[str, frozenset[str]]; POLICY_VERSION: str
```

## API
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/auth/login` | public | `?return_to=` (relative paths only) → 302 to Keycloak |
| GET | `/auth/callback` | public | Completes login, sets cookie |
| POST | `/auth/logout` | session | Revokes; returns Keycloak end-session URL |
| POST | `/auth/logout-all` | session | |
| GET | `/auth/me` | session | User, memberships (business id, name, roles), active business, permissions, csrf_token |
| POST | `/auth/active-business` | session | `{business_id}`; rotates session |
| GET | `/identity/members` | `identity.membership.read` | List staff with roles |
| POST | `/identity/invitations` | `identity.membership.write` | `{email, display_name, roles[]}`; returns a temporary password **only if SMTP is not configured** |
| POST | `/identity/invitations/{id}/revoke` | `identity.membership.write` | |
| PATCH | `/identity/members/{membership_id}` | `identity.membership.write` | Title, status (`active`/`suspended`/`removed`), version |
| PUT | `/identity/members/{membership_id}/roles` | `identity.membership.write` | Full role set; owner rule applies |

## Permissions
`identity.membership.read`, `identity.membership.write`, `identity.owner.grant`.

## Events
Published: `identity.membership_changed` (roles or status changed). Subscribed: none.

## Keycloak realm (`deploy/keycloak/realm-smb.json`)
Realm `smb`; registration disabled; email as username; password policy (length ≥ 12, not username, not email, history 3); brute-force detection on (lockout after 5 failures, 15 min); required action `CONFIGURE_TOTP` default; OTP policy TOTP SHA1 6 digits 30 s (authenticator app compatible). Client `smb-app`: confidential, standard flow only, PKCE S256, redirect URI `https://$DOMAIN/api/v1/auth/callback`, post-logout redirect `https://$DOMAIN/`. Client `smb-admin`: service account only, realm-management `manage-users` + `view-users`. Token lifetimes: access 5 min (only used at login), SSO session idle 30 min.

## Error codes
`identity.login_failed`, `identity.last_owner`, `identity.owner_grant_forbidden`, `identity.membership_inactive`, `identity.invitation_exists`, `identity.invitation_expired`, `identity.keycloak_unavailable`.

## UI
`/login`, `/select-business`, `/settings/staff` (list, invite dialog, role editor, suspend/remove with confirm), user menu (switch business, log out, log out everywhere).

## Tests (must exist)
- `test_callback_rejects_bad_state_nonce_and_expired_token` (mock JWKS)
- `test_first_login_accepts_matching_invitation`
- `test_permissions_recomputed_each_request_after_role_revocation`
- `test_cannot_remove_last_owner`; `test_admin_cannot_grant_owner`
- `test_session_idle_and_absolute_timeout`; `test_session_rotated_on_business_switch`
- `test_cannot_switch_to_business_without_active_membership`
- `test_return_to_rejects_absolute_urls` (open-redirect protection)
- Playwright: `login_with_totp_then_logout` (seeded test user and TOTP secret in the dev realm only)

## As built (P2, 2026-11-03)
- Inviting someone who already has an account (e.g. a member of another business) adds the membership immediately. Otherwise a Keycloak user is created with a temporary password shown once (no SMTP) and required actions UPDATE_PASSWORD then CONFIGURE_TOTP.
- An email already linked to a different identity-provider subject is refused at login (no silent account merge).
- Test helper `app.modules.identity.testing.login_as(app, business_id, roles)` creates real session rows.

## Out of scope
Custom roles; SSO with external identity providers (v1+ via Keycloak identity brokering); SCIM.

### Added in P3 (2026-11-03)
- `python -m app.cli dev-account --email <staff email>` (**dev and test only**; refuses when `APP_ENV=prod`): deletes any Keycloak user with that email, creates a new one with a temporary password (Keycloak forces a new password and TOTP setup) and links the app user to it. The end-to-end tests use it to give a seeded operator a login.
- The business picker's "Open" buttons are named after their business ("Open Example Cycle Works").

### Added 2026-11-03: demo logins on a production install
- `python -m app.cli demo-logins --out FILE [--business SLUG]... [--email ADDRESS]... [--reset-passwords] [--temporary-password]` (**works in production**, unlike `dev-account`) creates Keycloak accounts for the staff of the synthetic demo businesses. The seed makes them claimable `pending:` users, but Keycloak accounts exist only for people `bootstrap-business` or an invitation created, so on a real install nobody could sign in as Morgan or Riley.
- **Fenced:** only people listed in `data/fixtures.json` (synthetic, example.com) of businesses identified by slug **and** by the fixed id the seed gives them; any other business or person is refused (a member of a demo business who is not in the fixtures is never touched); refuses `DATA_PROFILE=regulated`; a person already bound to a Keycloak identity only gets an account if it is that one (otherwise "not usable", exit status 1).
- **Passwords:** random (the generator `bootstrap-business` uses), written only to FILE (mode 600, replaced atomically after every account, merged with an earlier run's lines) and never printed or logged; the output names the file and the e-mail addresses. Permanent by default (no forced change); `--temporary-password` makes Keycloak ask for a new one. CONFIGURE_TOTP stays mandatory either way.
- **Re-runs:** an account that exists is skipped; `--reset-passwords` issues a new password (the authenticator app is kept). `keycloak_admin.create_user` gained keyword options (`temporary`, `required_actions`, `existing_ok`) and `set_password`; existing callers are unchanged.
- Code: `identity/demo_logins.py` (Keycloak side), `app/demo_logins.py` (composition root: fixtures, scope, file). Tests: `identity/tests/test_demo_logins.py` (requests to Keycloak, who may sign in), `tests/integration/test_demo_logins.py` (the command on the seeded data). Procedure: [install.md](../../runbooks/install.md#demo-installs-only-logins-for-the-demo-staff-morgan-riley-and-the-others).
