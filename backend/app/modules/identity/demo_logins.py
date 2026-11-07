"""Keycloak logins for the staff of the synthetic demo businesses (CLI `demo-logins`, runbooks/install.md).

The seed creates the demo staff as users with a claimable `pending:` identity: the first Keycloak sign-in with the
same e-mail claims it (`login._upsert_user`). On a real install no Keycloak account exists for them, because only
`bootstrap-business` and invitations create accounts, and `dev-account` refuses production. This module creates the
accounts. It knows nothing about *which* people may get one: the composition root (`app/demo_logins.py`) passes only
people listed in `data/fixtures.json`, of businesses it has identified by slug and fixed id.

Everything goes through `keycloak_admin`, the code `bootstrap-business` and invitations use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.platform.context import Context

from . import keycloak_admin, public, repository
from .login import PENDING_PREFIX

State = Literal["created", "reset", "exists", "conflict"]


@dataclass(frozen=True)
class StaffIdentity:
    """A staff member as the application knows them. `linked_subject` is None until their first sign-in."""

    email: str
    display_name: str
    linked_subject: str | None
    active: bool


@dataclass(frozen=True)
class DemoLogin:
    state: State
    password: str | None = None  # a secret: only for the credentials file, never for output or logs
    reason: str = ""

    def __repr__(self) -> str:  # a stray print or log line must not carry the password
        return f"DemoLogin(state={self.state!r}, password={'<set>' if self.password else None})"


def active_staff(db: Session, ctx: Context) -> dict[str, StaffIdentity]:
    """Active members of ctx's business by lower-case e-mail."""
    rows: list[dict[str, Any]] = repository.active_members_identity(db, ctx)
    return {
        r["email"]: StaffIdentity(
            email=r["email"],
            display_name=r["display_name"],
            linked_subject=None if str(r["oidc_subject"]).startswith(PENDING_PREFIX) else str(r["oidc_subject"]),
            active=r["user_status"] == "active",
        )
        for r in rows
    }


def ensure_judge_membership(db: Session, ctx: Context, email: str, display_name: str) -> None:
    """Add only the judge to an already validated seeded business; preserve every other member and demo record.

    Use the seed's claimable-user path, retain a linked subject, and write only missing grants/memberships or a
    necessary membership reactivation. The caller validates ALL demo business slugs/ids before any writes.
    """
    repository.lock_owner_changes(db, ctx)
    user_id = public.ensure_user(db, email, display_name)
    user = repository.get_user(db, user_id)
    assert user is not None
    if user.status != "active":
        raise SystemExit("refusing: judge user is disabled; no login provisioned")
    membership = repository.get_membership_for_user(db, ctx, user_id)
    if membership is not None and repository.membership_roles(db, ctx, membership.id) - {"demo_reviewer"}:
        raise SystemExit("refusing: judge has unexpected roles; review its grants before making a public login")
    public.ensure_membership(db, ctx, user_id, ["demo_reviewer"], "Demo reviewer")
    membership = repository.get_membership_for_user(db, ctx, user_id)
    assert membership is not None
    if membership.status != "active":
        membership.status = "active"
        membership.version += 1
    if user.display_name != display_name:
        user.display_name = display_name
    db.flush()


def ensure_demo_login(person: StaffIdentity, *, reset_passwords: bool, temporary_password: bool) -> DemoLogin:
    """Give one person a Keycloak login. Idempotent: an account that exists is left alone unless `reset_passwords`.

    The password is random (`keycloak_admin.temporary_password`, the generator `bootstrap-business` uses) and permanent
    unless `temporary_password`: a demo has no time for a forced change. The authenticator app stays mandatory.
    Never creates an account that could not sign in: a person already bound to a Keycloak identity needs that very
    account (`login._upsert_user` refuses a different one), so a missing or different account is reported as a conflict.
    """
    existing = keycloak_admin.find_user_id(person.email)
    if person.linked_subject is not None:
        if existing is None:
            return DemoLogin("conflict", reason="signed in before, but its Keycloak account no longer exists")
        if existing != person.linked_subject:
            return DemoLogin("conflict", reason="the Keycloak account is not the one this person signed in with")
    if existing is not None:
        if not reset_passwords:
            return DemoLogin("exists")
        password = keycloak_admin.temporary_password()
        keycloak_admin.set_password(existing, password, temporary=temporary_password)
        return DemoLogin("reset", password)
    password = keycloak_admin.temporary_password()
    keycloak_admin.create_user(
        person.email,
        person.display_name,
        password,
        temporary=temporary_password,
        required_actions=keycloak_admin.REQUIRED_ACTIONS if temporary_password else keycloak_admin.TOTP_ONLY,
        existing_ok=False,  # an account that appeared meanwhile has another password: do not claim it is ours
    )
    return DemoLogin("created", password)


def ensure_judge_login(person: StaffIdentity, password: str, *, reset_password: bool) -> DemoLogin:
    """Same identity conflict rules as staff; fixed env password, no self-service or second factor."""
    existing = keycloak_admin.find_user_id(person.email)
    if person.linked_subject is not None and existing != person.linked_subject:
        return DemoLogin("conflict", reason="the Keycloak account is missing or differs from the linked identity")
    state: State = "exists"
    if existing is None:
        existing = keycloak_admin.create_user(
            person.email,
            person.display_name,
            password,
            temporary=False,
            required_actions=[],
            existing_ok=False,
        )
        state = "created"
    # Strip account authority before resetting a password; retrying repairs a partially completed setup.
    keycloak_admin.harden_judge_user(existing)
    if state == "exists" and reset_password:
        keycloak_admin.reset_judge_password(existing, password)
        state = "reset"
    return DemoLogin(state)
