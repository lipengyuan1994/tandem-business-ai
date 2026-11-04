"""`python -m app.cli demo-logins`: Keycloak logins for the staff of the synthetic demo businesses (production-safe).

The seed creates Morgan, Riley and the other demo staff as application users that claim a login the first time
someone signs in with their e-mail. On a real install Keycloak has no such accounts (only `bootstrap-business` and
invitations create them, and `dev-account` refuses production), so nobody can sign in to the demo businesses. This
command creates the accounts. Unlike `dev-account` it works on a production install, so it is fenced in:

* WHO: only staff listed in `data/fixtures.json` (synthetic, example.com), of businesses identified by their slug AND by
  the fixed id the seed gives them. Any other business is refused; a person not in the fixtures is never touched, even
  when they are a member of a demo business.
* WHEN: never under the regulated data profile (an install for a real customer's records).
* PASSWORDS: random, written only to the `--out` file (mode 600, replaced atomically, written after every account so a
  later failure cannot lose one), never printed or logged. The output names the file and the e-mail addresses.
* RE-RUNS: an account that exists is left alone unless `--reset-passwords`.

Composition root (like seed.py): it joins the fixtures, the identity module and the file system.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

from app.main import build_registry
from app.modules.identity import demo_logins as identity_logins
from app.modules.identity.demo_logins import DemoLogin, StaffIdentity
from app.platform.config import get_settings, is_regulated
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import DomainError
from app.platform.ids import fixture_id
from app.seed import DEFAULT_FIXTURES

DEMO_DOMAIN = "@example.com"
FILE_HEADER = (
    "# Demo logins for the synthetic demo businesses. SECRET: keep this file private (mode 600), delete it when the\n"
    "# demo is over. One account per line: e-mail address, a space, password.\n"
    "# At the first sign-in Keycloak asks for an authenticator app (TOTP): scan the QR code once.\n"
)


@dataclass(frozen=True)
class DemoPerson:
    email: str
    display_name: str


@dataclass(frozen=True)
class DemoBusiness:
    key: str  # fixture key; the seed derives the business id from it
    slug: str
    staff: tuple[DemoPerson, ...]


# ------------------------------------------------------------------------------------------------ who is allowed
def load_demo_businesses(path: Path = DEFAULT_FIXTURES) -> dict[str, DemoBusiness]:
    """The demo businesses and their active staff, by slug, exactly as the fixtures list them."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("synthetic") is not True:
        raise SystemExit("refusing: the fixtures are not marked synthetic")
    tables = doc["tables"]
    users = {u["id"]: u for u in tables["users"]}
    found: dict[str, DemoBusiness] = {}
    for business in tables["businesses"]:
        staff: list[DemoPerson] = []
        for membership in tables["memberships"]:
            if membership["business_id"] != business["id"] or membership.get("status", "active") != "active":
                continue
            user = users[membership["user_id"]]
            email = str(user["email"]).lower()
            if not email.endswith(DEMO_DOMAIN):
                raise SystemExit(f"refusing: fixture address {email} is not an example.com address")
            staff.append(DemoPerson(email, str(user["display_name"])))
        found[business["slug"]] = DemoBusiness(business["id"], business["slug"], tuple(staff))
    return found


def choose(
    businesses: dict[str, DemoBusiness], slugs: list[str] | None, emails: list[str] | None
) -> list[DemoBusiness]:
    """The businesses (and people) the operator asked for; anything that is not demo is a refusal."""
    wanted = list(dict.fromkeys(slugs or businesses))
    unknown = [slug for slug in wanted if slug not in businesses]
    if unknown:
        raise SystemExit(
            f"refusing: {', '.join(repr(s) for s in unknown)} is not a demo business. This command only creates logins "
            f"for the synthetic demo businesses of data/fixtures.json ({', '.join(businesses)})."
        )
    chosen = [businesses[slug] for slug in wanted]
    if emails:
        only = {e.strip().lower() for e in emails}
        allowed = {p.email for b in chosen for p in b.staff}
        strangers = sorted(only - allowed)
        if strangers:
            raise SystemExit(
                f"refusing: {', '.join(strangers)} is not demo staff of {', '.join(b.slug for b in chosen)}. "
                f"Demo staff: {', '.join(sorted(allowed))}."
            )
        chosen = [replace(b, staff=tuple(p for p in b.staff if p.email in only)) for b in chosen]
        chosen = [b for b in chosen if b.staff]
    return chosen


# ------------------------------------------------------------------------------------ the credentials file
def read_credentials(path: Path) -> dict[str, str]:
    """E-mail -> password from an earlier run. Comment lines and anything that is not `address password` are ignored."""
    if not path.is_file():
        return {}
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and "@" in parts[0] and not line.lstrip().startswith("#"):
            found[parts[0].lower()] = parts[1]
    return found


def write_credentials(path: Path, credentials: dict[str, str]) -> None:
    """Replace the file atomically; it exists with mode 600 from its first byte, whatever the umask."""
    body = FILE_HEADER + "".join(f"{email} {password}\n" for email, password in sorted(credentials.items()))
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise


def require_writable(path: Path) -> None:
    """Fail before any account exists if the passwords would have nowhere to go (no mount, read-only, wrong user)."""
    if path.is_dir():
        raise SystemExit(f"refusing: --out {path} is a directory; name the file, for example {path / 'logins.txt'}")
    if not path.parent.is_dir():
        raise SystemExit(
            f"refusing: the folder {path.parent} does not exist (on a VM it is a mounted folder, see install.md)"
        )
    probe = path.with_name(f".{path.name}.{os.getpid()}.probe")
    try:
        os.close(os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError as exc:
        raise SystemExit(
            f"refusing: cannot create files in {path.parent} ({exc.strerror}); no account was created"
        ) from exc
    finally:
        with contextlib.suppress(FileNotFoundError):
            probe.unlink()


# --------------------------------------------------------------------------------------------------- the command
def _targets(chosen: list[DemoBusiness]) -> tuple[list[StaffIdentity], dict[str, str]]:
    """The people to log in, from the database, and those skipped with the reason. Refuses an unfamiliar business."""
    resolver = build_registry().public_business_resolver
    assert resolver is not None
    problems: list[str] = []
    ids: dict[str, UUID] = {}
    for business in chosen:
        business_id = resolver(business.slug)
        if business_id is None:
            problems.append(f"{business.slug} does not exist; run `seed` first")
        elif business_id != fixture_id(business.key):
            problems.append(f"{business.slug} exists but is not the seeded demo business (its id differs)")
        else:
            ids[business.slug] = business_id
    if problems:
        raise SystemExit("refusing: " + "; ".join(problems) + ". Nothing was changed.")
    targets: dict[str, StaffIdentity] = {}
    skipped: dict[str, str] = {}
    for business in chosen:
        ctx = Context(request_id="cli", actor_kind="system", user_id=None, business_id=ids[business.slug])
        with transaction(ctx) as db:
            members = identity_logins.active_staff(db, ctx)
        for person in business.staff:
            member = members.get(person.email)
            if person.email in targets:
                continue  # a person on the staff of both businesses
            if member is None:
                skipped.setdefault(person.email, f"not an active member of {business.slug}")
            elif not member.active:
                skipped[person.email] = "the user is disabled"
            else:
                targets[person.email] = member
                skipped.pop(person.email, None)
    return list(targets.values()), skipped


def _report(
    out: Path,
    outcomes: dict[str, DemoLogin],
    skipped: dict[str, str],
    *,
    wrote: bool,
    reset_passwords: bool,
) -> list[str]:
    """What to print: the file and e-mail addresses only. Nothing here may contain a password."""
    by_state: dict[str, list[str]] = {"created": [], "reset": [], "exists": [], "conflict": []}
    for email, outcome in outcomes.items():
        by_state[outcome.state].append(email)
    lines = [
        f"demo logins: {len(by_state['created'])} created, {len(by_state['reset'])} password reset, "
        f"{len(by_state['exists'])} already existed"
    ]
    if wrote:
        lines.append(f"credentials file: {out} (mode 600; e-mail address, then password)")
    else:
        state = "unchanged" if out.exists() else "not created"
        lines.append(f"credentials file: {out} ({state}: no new password was issued)")
    if by_state["created"]:
        lines.append("created: " + ", ".join(by_state["created"]))
    if by_state["reset"]:
        lines.append("password reset: " + ", ".join(by_state["reset"]))
    if by_state["exists"]:
        hint = "" if reset_passwords else " (add --reset-passwords to issue a new password)"
        lines.append("already had a login, left as it was: " + ", ".join(by_state["exists"]) + hint)
    for email in by_state["conflict"]:
        lines.append(f"not usable: {email}: {outcomes[email].reason}")
    for email, reason in skipped.items():
        lines.append(f"skipped: {email}: {reason}")
    return lines


def run(
    out: Path,
    *,
    businesses: list[str] | None = None,
    emails: list[str] | None = None,
    reset_passwords: bool = False,
    temporary_password: bool = False,
    fixtures: Path = DEFAULT_FIXTURES,
) -> int:
    """Returns the exit status: 0 when every requested login works or already did, 1 otherwise."""
    if is_regulated():
        raise SystemExit(
            "refusing: DATA_PROFILE=regulated. Demo logins belong to installs that hold only the synthetic demo data; "
            "a regulated install holds a real customer's records."
        )
    chosen = choose(load_demo_businesses(fixtures), businesses, emails)
    if not chosen:
        raise SystemExit("refusing: no demo staff selected")
    out = Path(os.path.abspath(out))
    require_writable(out)
    settings = get_settings()
    if not settings.oidc_internal_issuer_url or not settings.keycloak_admin_client_secret.get_secret_value():
        raise SystemExit(
            "refusing: Keycloak admin access is not configured (OIDC_INTERNAL_ISSUER_URL, KEYCLOAK_ADMIN_*)"
        )
    targets, skipped = _targets(chosen)

    credentials = read_credentials(out)
    outcomes: dict[str, DemoLogin] = {}
    wrote = False
    failure: str | None = None
    try:
        for person in targets:
            outcome = identity_logins.ensure_demo_login(
                person, reset_passwords=reset_passwords, temporary_password=temporary_password
            )
            outcomes[person.email] = outcome
            if outcome.password:
                credentials[person.email] = outcome.password
                write_credentials(out, credentials)  # now, not at the end: a later failure must not lose it
                wrote = True
    except DomainError as exc:
        failure = f"{exc.code}" + (f" ({exc.detail})" if exc.detail else "")
    for line in _report(out, outcomes, skipped, wrote=wrote, reset_passwords=reset_passwords):
        print(line)
    if failure:
        raise SystemExit(
            f"stopped: {failure}. Whatever was created before this is listed above, with its password in the file."
        )
    return 1 if any(o.state == "conflict" for o in outcomes.values()) else 0
