"""Keycloak logins for the staff of the synthetic demo businesses (CLI `demo-logins`, runbooks/install.md).

The seed creates the demo staff as users with a claimable `pending:` identity: the first Keycloak sign-in with the
same e-mail claims it (`login._upsert_user`). On a real install no Keycloak account exists for them, because only
`bootstrap-business` and invitations create accounts, and `dev-account` refuses production. This module creates the
accounts. It knows nothing about *which* people may get one: the composition root (`app/demo_logins.py`) passes only
people listed in `data/fixtures.json`, of businesses it has identified by slug and fixed id.

Everything goes through `keycloak_admin`, the code `bootstrap-business` and invitations use.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.db import SET_TENANT

from . import keycloak_admin, public, repository
from .login import PENDING_PREFIX
from .models import Membership

State = Literal["created", "reset", "exists", "conflict"]


@dataclass(frozen=True)
class StaffIdentity:
    """A staff member as the application knows them. `linked_subject` is None until their first sign-in."""

    email: str
    display_name: str
    linked_subject: str | None
    active: bool
    staging_email: str | None = None


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


GUEST_EMAIL = "demo.guest@example.com"
LEGACY_EMAIL = "judge@example.com"


def prepare_public_guest(db: Session, business_ids: list[UUID]) -> StaffIdentity:
    """Prepare all app records atomically. Preserve the same app ID and OIDC subject across upgrades."""
    first = Context(request_id="cli", actor_kind="system", user_id=None, business_id=business_ids[0])
    repository.lock_owner_changes(db, first)
    previous = repository.get_user_by_email(db, LEGACY_EMAIL)
    guest = repository.get_user_by_email(db, GUEST_EMAIL)
    if previous is not None and guest is not None:
        raise SystemExit("refusing: both demo addresses have app accounts")
    if repository.pending_invitations_for_email(db, GUEST_EMAIL):
        raise SystemExit("refusing: guest address has outstanding invitations")
    user = previous or guest
    if user is not None:
        if user.status != "active":
            raise SystemExit("refusing: guest user is disabled")
        # Own-membership RLS exposes affiliations across tenants, including inactive ones.
        db.execute(SET_TENANT, {"b": "", "u": str(user.id)})
        affiliations = set(db.scalars(select(Membership.business_id).where(Membership.user_id == user.id)))
        if affiliations.difference(business_ids):
            raise SystemExit("refusing: guest has a membership outside the synthetic demo")
        if not affiliations or user.display_name != ("Demo Judge" if previous else "Demo Guest"):
            raise SystemExit("refusing: unrecognized demo identity")
    if previous is not None:
        if previous.oidc_subject.startswith(PENDING_PREFIX) and previous.oidc_subject != PENDING_PREFIX + LEGACY_EMAIL:
            raise SystemExit("refusing: unrecognized legacy demo identity")
        old_subject = keycloak_admin.find_user_id(LEGACY_EMAIL)
        new_subject = keycloak_admin.find_user_id(GUEST_EMAIL)
        if new_subject is not None or (
            not previous.oidc_subject.startswith(PENDING_PREFIX) and previous.oidc_subject != old_subject
        ):
            raise SystemExit("refusing: conflicting Keycloak identity for the legacy demo account")
        if old_subject and previous.oidc_subject.startswith(PENDING_PREFIX):
            if not keycloak_admin.is_legacy_demo_guest(old_subject, LEGACY_EMAIL):
                raise SystemExit("refusing: unrecognized legacy Keycloak profile")
        previous.email = GUEST_EMAIL
        previous.display_name = "Demo Guest"
        previous.oidc_subject = old_subject or PENDING_PREFIX + GUEST_EMAIL
        db.flush()

    elif (guest is None or guest.oidc_subject.startswith(PENDING_PREFIX)) and (
        keycloak_admin.find_user_id(GUEST_EMAIL) or keycloak_admin.find_user_id(LEGACY_EMAIL)
    ):
        raise SystemExit("refusing: a preexisting Keycloak account is not linked to this guest")

    for business_id in business_ids:
        ctx = Context(request_id="cli", actor_kind="system", user_id=None, business_id=business_id)
        db.execute(SET_TENANT, {"b": str(business_id), "u": ""})
        ensure_judge_membership(db, ctx, GUEST_EMAIL, "Demo Guest")
    guest = repository.get_user_by_email(db, GUEST_EMAIL)
    assert guest is not None
    return replace(active_staff(db, ctx)[GUEST_EMAIL], staging_email=f"tandem-setup-{guest.id}@example.com")


def bind_public_guest(db: Session, person: StaffIdentity, password: str) -> StaffIdentity:
    """Commit the subject before making the public address usable. The reserved staging address survives retries."""
    from .models import User

    guest = db.scalar(select(User).where(User.email == GUEST_EMAIL).with_for_update())
    assert guest is not None and person.staging_email == f"tandem-setup-{guest.id}@example.com"
    if guest.oidc_subject == PENDING_PREFIX + GUEST_EMAIL:
        if keycloak_admin.find_user_id(GUEST_EMAIL) or keycloak_admin.find_user_id(LEGACY_EMAIL):
            raise SystemExit("refusing: public address already belongs to an unlinked identity")
        subject = keycloak_admin.find_user_id(person.staging_email)
        if subject is None:
            subject = keycloak_admin.create_user(
                person.staging_email,
                person.display_name,
                password,
                temporary=False,
                required_actions=[],
                existing_ok=False,
            )
        guest.oidc_subject = subject
    return replace(person, linked_subject=guest.oidc_subject)


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
        raise SystemExit("refusing: guest user is disabled; no login provisioned")
    membership = repository.get_membership_for_user(db, ctx, user_id)
    if membership is not None and repository.membership_roles(db, ctx, membership.id) - {"demo_reviewer"}:
        raise SystemExit("refusing: guest has unexpected roles; review its grants before making a public login")
    public.ensure_membership(db, ctx, user_id, ["demo_reviewer"], "Demo guest")
    membership = repository.get_membership_for_user(db, ctx, user_id)
    assert membership is not None
    if membership.title == "Demo reviewer":
        membership.title = "Demo guest"
        membership.version += 1
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
    legacy = keycloak_admin.find_user_id(LEGACY_EMAIL)
    if legacy is not None:
        if existing is not None or legacy != person.linked_subject:
            return DemoLogin("conflict", reason="legacy Keycloak identity does not match the prepared guest")
        # The app transaction already pinned this exact subject. A retry can find it at either address.
        keycloak_admin.rename_public_guest(legacy, GUEST_EMAIL)
        existing = keycloak_admin.find_user_id(person.email)
        if existing != legacy:
            return DemoLogin("conflict", reason="guest address was not applied to the original Keycloak user")
    state: State = "exists"
    if existing is None and person.staging_email and person.linked_subject:
        staging = keycloak_admin.find_user_id(person.staging_email)
        if staging == person.linked_subject:
            keycloak_admin.harden_judge_user(staging)
            keycloak_admin.rename_public_guest(staging, person.email)
            existing = keycloak_admin.find_user_id(person.email)
            state = "created"
    if existing is None or existing != person.linked_subject:
        return DemoLogin("conflict", reason="the Keycloak account is missing or differs from the linked identity")
    # Strip account authority before resetting a password; retrying repairs a partially completed setup.
    if state != "created":
        keycloak_admin.harden_judge_user(existing)
    if reset_password:
        keycloak_admin.reset_judge_password(existing, password)
        state = "reset"
    return DemoLogin(state)
