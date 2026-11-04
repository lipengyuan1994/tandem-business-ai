# P2 · Core services (Nov 3) — PRE

**Goal:** real login with MFA, businesses and roles, audit browsing, the approvals engine and the outbox, all proven end to end with a fake executor.
**Entry:** P1 exit gate.

### P2-01 · org module
Kind: PRE · Tier: B · Module: M02 · Size: M · Depends on: P1-13
Read first: [M02](../modules/M02-org.md), [04](../04-repository-and-conventions.md)
Create: `app/modules/org/*` (full template), migration for `locations`, `opening_hours`, `business_settings` (with row-level security).
Steps: settings Pydantic model with the defaults table. Register the public-business resolver with the platform registry. Implement every endpoint and the public profile.
Done when:
- [ ] All tests listed in M02 pass, plus the standard ones (denied role, cross-business, version conflict).

### P2-02 · identity: tables, roles, sessions
Kind: PRE · Tier: A · Module: M01 · Size: M · Depends on: P2-01
Read first: [M01](../modules/M01-identity.md), [06](../06-security-and-privacy.md)
Create: `app/modules/identity/{models,repository,service,roles,sessions,registration,public}.py`, migration (`users`, `sessions`, `memberships`, `role_grants`, `invitations`; special row-level security policies from [05](../05-data-model.md#tenancy-and-row-level-security)).
Steps:
1. `roles.py`: the permission bundles exactly as in the [06 matrix](../06-security-and-privacy.md#permission-matrix), plus `POLICY_VERSION = "2026-11-03.1"`.
2. The session resolver (cookie → session → user → active membership → permissions) is registered with the platform, replacing the fake in non-test settings.
3. Update `platform/testing/auth.py` so `login_as` creates real session rows.

Done when:
- [ ] `test_permissions_recomputed_each_request_after_role_revocation`, `test_session_idle_and_absolute_timeout`, `test_cannot_switch_to_business_without_active_membership` pass.

### P2-03 · identity: Keycloak OIDC login (backend-for-frontend)
Kind: PRE · Tier: A · Module: M01 · Size: L · Depends on: P2-02
Read first: [06 Authentication flow](../06-security-and-privacy.md#authentication-flow-keycloak-backend-for-frontend), [M01 API and realm](../modules/M01-identity.md#api)
Create: `app/modules/identity/oidc.py`, `api_auth.py`; finalise `deploy/keycloak/realm-smb.json`.
Steps: authorisation code + PKCE with `authlib`; state, nonce and verifier stored server-side for 10 minutes in identity's `oidc_login_states` table; ID token validation with cached JWKS; login, callback, logout, logout-all, `/auth/me`, active-business switch; `return_to` accepts relative paths only.
Done when:
- [ ] `test_callback_rejects_bad_state_nonce_and_expired_token` (mocked JWKS) and `test_return_to_rejects_absolute_urls` pass.
- [ ] Manual: log in through `https://localhost:8443/api/v1/auth/login` as the seeded dev owner with TOTP; `/auth/me` shows the business and permissions.

### P2-04 · identity: staff, invitations, bootstrap CLI
Kind: PRE · Tier: A · Module: M01 · Size: M · Depends on: P2-03
Create: `app/modules/identity/{keycloak_admin.py,api_members.py}`, `app/cli.py` command `bootstrap-business`.
Steps: Keycloak admin client using the `smb-admin` service account (create user, set required actions, temporary password, disable user). Member and role endpoints with the owner rules. Invitations.
Done when:
- [ ] `test_first_login_accepts_matching_invitation`, `test_cannot_remove_last_owner`, `test_admin_cannot_grant_owner` pass (Keycloak admin mocked with `respx`).
- [ ] `python -m app.cli bootstrap-business --name "Example Cycle Works" --slug example-cycle-works --timezone America/Denver --currency USD --owner-email owner@example.com --owner-name "Morgan Example"` works against the dev stack.

### P2-05 · audit module
Kind: PRE · Tier: B · Module: M09 · Size: S · Depends on: P2-02
Read first: [M09](../modules/M09-audit.md)
Done when:
- [ ] M09 tests pass.

### P2-06 · approvals engine
Kind: PRE · Tier: A · Module: M08 · Size: L · Depends on: P2-02, P1-07, P1-08
Read first: [07 Proposals](../07-workflow-engine.md#proposals), [M08](../modules/M08-approvals.md)
Create: `app/modules/approvals/*`, migration for the four tables (append-only grants on revisions and decisions).
Steps: implement create, revise, decide, cancel, list and detail, action-types (JSON schema from each `payload_model`), the `approvals.execute` job with the TX-1/2/3 algorithm, the expiry cron and events. Add a **test-only executor** `testing.noop_write` (registered only when `APP_ENV=test`) that writes a row to a test table and can be told to fail or crash.
Done when:
- [ ] All tests listed in M08 pass, including the concurrency and crash-retry tests.
- [ ] Tier-A review recorded.

### P2-07 · notifications: outbox and in-app
Kind: PRE · Tier: A · Module: M10 · Size: M · Depends on: P2-06
Read first: [07 Outbox](../07-workflow-engine.md#outbox-m10-notifications), [M10](../modules/M10-notifications.md)
Create: `app/modules/notifications/*` with adapters `simulated`, `web_chat` (`smtp` is a stub that raises "not configured" until P8-02), sandboxed template rendering, a delivery loop registered with the worker, and the subscriber for `approvals.proposal_created`.
Done when:
- [ ] All tests listed in M10 pass.

### P2-08 · Frontend: authentication and settings
Kind: PRE · Tier: B · Module: frontend (identity, org) · Size: L · Depends on: P2-03, P2-04, P2-01, P1-12
Read first: [09](../09-frontend.md), [M01 UI](../modules/M01-identity.md#ui), [M02 UI](../modules/M02-org.md#ui)
Create: `src/features/identity/*` (auth guard using `/auth/me`, select-business page, user menu, staff page with invite and role editor), `src/features/org/*` (business, locations and hours editor, policies form).
Done when:
- [ ] Component tests for each page (loading, empty, data, error, permission-hidden states) pass.
- [ ] Manual: log in, switch business, invite a user, edit hours.

### P2-09 · Frontend: approvals, audit, notifications
Kind: PRE · Tier: B · Module: frontend (approvals, audit, notifications) · Size: L · Depends on: P2-06, P2-05, P2-07
Read first: [M08 UI](../modules/M08-approvals.md#ui), [09 Design system](../09-frontend.md#design-system)
Create: approvals queue and detail (`PreviewDiff`, `EvidenceList`, revision history, generic edit form from the action type JSON schema, approve/reject with comment, execution and delivery status), audit page, notification bell.
Done when:
- [ ] Component tests pass. Manual: with the test executor in the dev stack (`APP_ENV=dev` may register it behind a `DEV_TEST_EXECUTORS=1` flag), create → edit → approve → see succeeded.

### P2-10 · Integration test: proposal lifecycle through the API
Kind: PRE · Tier: A · Module: tests · Size: S · Depends on: P2-06, P2-07
Create: `backend/tests/integration/test_proposal_lifecycle.py`
Done when:
- [ ] Covers create → revise → approve old revision (409) → approve current → worker runs → audit rows (proposal created, decided, executed) → outbox row delivered (simulated) → events delivered.

## Exit gate P2
- [ ] Login with TOTP works in the dev stack; roles change effect immediately.
- [ ] Approvals engine tests (including concurrency and crash) green; Tier-A review recorded.
- [ ] `make check` green; P2 entries in `PRE_EVENT_WORK.md`.
