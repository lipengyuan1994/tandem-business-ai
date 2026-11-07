# identity (M01) — module card

Spec: [docs/roadmap/modules/M01-identity.md](../../../../docs/roadmap/modules/M01-identity.md)

- Tables: `users`, `sessions`, `oidc_login_states` (global); `memberships`, `role_grants`, `invitations` (row-level security; own memberships visible across businesses).
- Registers: session resolver (`sessions.resolve`), routers `/auth/*` (login and callback are public), `/identity/*`.
- Public (`public.py`): `get_user_brief`, `get_user_briefs`, `active_roles`, `effective_permissions`, `has_permission_now`, `list_users_with_permission`, `ROLES`, `POLICY_VERSION`.
- Roles → permissions: `roles.py` (06 matrix). Keycloak: `oidc.py` (login), `keycloak_admin.py` (create users), `login.py` (callback → session).
- Test helpers: `testing.make_user`, `add_member`, `login_as`.
- CLI: `python -m app.cli bootstrap-business ...`; `demo-logins` (Keycloak logins for the demo staff of `data/fixtures.json`, production-safe, passwords to a mode-600 file: `demo_logins.py` and `app/demo_logins.py`); `dev-account` (dev and test only).

- P8-23: regulated staff sessions use 15-minute idle / 8-hour absolute limits (including existing sessions); standard settings unchanged.
- Session `authenticated_at` preserves the authentication origin across rotations. Regulated expiry stays capped at origin + 8 hours; standard expiry preserves the original configured absolute deadline.
- Login state is keyed by the OIDC state plus a short-lived Secure, HttpOnly, SameSite=Lax `__Host-smb_oidc` browser cookie. A successful login revokes only the presented session before issuing its replacement.
- Owner status changes require owner authority, including reactivation. Membership and role edits share a business-scoped transaction lock before checking the last-active-owner invariant.

P10-D: public.dictation_names(db, ctx, entity_ids) returns bounded permission/business-scoped names only.

P10-J: `demo_reviewer` = admin minus org writes and membership writes; policy 2026-11-07.1.
`demo-logins --judge` uses the env public password, clears actions/OTP and removes Keycloak roles/groups;
`--reset-password` repairs the fixed password (including password history). Staff mode excludes the judge.
Fixtures restore judge memberships in all three businesses on reset, preserving the linked global user.
`--judge` also ensures the fixture app user and active reviewer memberships/grants in all three validated demo
businesses without seed/reset, preserving other app rows and linked subjects; reruns leave app rows unchanged.
