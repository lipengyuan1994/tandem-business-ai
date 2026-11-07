"""Command line: `python -m app.cli <command>`.

Commands: openapi, bootstrap-business, seed, reset-demo, demo-logins, dev-account.
"""

from __future__ import annotations

import argparse
import json
import sys


def cmd_openapi(_: argparse.Namespace) -> None:
    from app.main import create_app

    print(json.dumps(create_app().openapi(), indent=2, sort_keys=True))


def cmd_bootstrap_business(args: argparse.Namespace) -> None:
    from app.main import build_registry
    from app.modules.identity.service import bootstrap_business
    from app.platform.context import Context
    from app.platform.db import transaction

    build_registry()
    with transaction(Context(request_id="cli", actor_kind="system", user_id=None, business_id=None)) as db:
        result = bootstrap_business(
            db,
            name=args.name,
            slug=args.slug,
            timezone=args.timezone,
            currency=args.currency,
            locale=args.locale,
            owner_email=args.owner_email,
            owner_name=args.owner_name,
        )
    print(f"business_id={result.business_id} owner={result.owner_email}")
    if result.temporary_password:
        # Printed once for the operator to hand to the owner; Keycloak forces a change and TOTP setup at first login.
        print(f"temporary_password={result.temporary_password}")
    else:
        print("owner already had an account; no password issued")


def cmd_seed(args: argparse.Namespace) -> None:
    from pathlib import Path

    from app.seed import DEFAULT_FIXTURES, seed

    result = seed(Path(args.file) if args.file else DEFAULT_FIXTURES, do_reset=args.reset)
    for slug, counts in result.items():
        print(slug, ", ".join(f"{k}={v}" for k, v in counts.items()))


def cmd_reset_demo(args: argparse.Namespace) -> None:
    """Re-seed the two synthetic demo businesses (and only those). Demo and rehearsal installs, never real customers."""
    from app.seed import DEFAULT_FIXTURES, seed

    if not args.yes:
        raise SystemExit("reset-demo deletes and recreates the demo businesses (example-*-works/-store). Add --yes.")
    for slug, counts in seed(DEFAULT_FIXTURES, do_reset=True).items():
        print(slug, ", ".join(f"{k}={v}" for k, v in counts.items()))


def cmd_demo_logins(args: argparse.Namespace) -> None:
    """Keycloak logins for the staff of the synthetic demo businesses. Production-safe; see app/demo_logins.py."""
    from pathlib import Path

    from app.demo_logins import run

    status = run(
        Path(args.out) if args.out else None,
        judge=args.judge,
        businesses=args.business,
        emails=args.email,
        reset_passwords=args.reset_passwords,
        temporary_password=args.temporary_password,
    )
    if status:
        raise SystemExit(status)


def cmd_dev_account(args: argparse.Namespace) -> None:
    from app.main import build_registry
    from app.modules.identity.service import dev_reset_account
    from app.platform.context import Context
    from app.platform.db import transaction

    build_registry()
    with transaction(Context(request_id="cli", actor_kind="system", user_id=None, business_id=None)) as db:
        password = dev_reset_account(db, args.email)
    # For scripts (end-to-end setup) to capture; Keycloak forces a new password and TOTP setup at first login.
    print(f"temporary_password={password}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("openapi", help="print the OpenAPI document").set_defaults(func=cmd_openapi)
    boot = sub.add_parser("bootstrap-business", help="create a business and its owner (Keycloak account + membership)")
    boot.add_argument("--name", required=True)
    boot.add_argument("--slug", required=True)
    boot.add_argument("--timezone", required=True)
    boot.add_argument("--currency", default="USD")
    boot.add_argument("--locale", default="en-US")
    boot.add_argument("--owner-email", required=True)
    boot.add_argument("--owner-name", required=True)
    boot.set_defaults(func=cmd_bootstrap_business)
    seed_cmd = sub.add_parser("seed", help="load data/fixtures.json (synthetic demo data) through the services")
    seed_cmd.add_argument("--reset", action="store_true", help="delete the demo businesses first")
    seed_cmd.add_argument("--file", default=None)
    seed_cmd.set_defaults(func=cmd_seed)
    reset = sub.add_parser(
        "reset-demo", help="delete and re-create the synthetic demo businesses (needs DATABASE_MIGRATION_URL)"
    )
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=cmd_reset_demo)
    logins = sub.add_parser(
        "demo-logins",
        help="create Keycloak logins for the staff of the synthetic demo businesses (works on production installs)",
        description=(
            "Creates Keycloak accounts only for the demo staff listed in data/fixtures.json, of the seeded demo "
            "businesses; refuses any other business and the regulated data profile. Random passwords go to --out "
            "(mode 600) and are never printed; the output lists the file and the e-mail addresses."
        ),
    )
    logins.add_argument("--out", help="file for the passwords (mode 600); a mounted folder in a container")
    logins.add_argument(
        "--business",
        action="append",
        metavar="SLUG",
        help="a demo business slug (repeatable); default: all demo businesses",
    )
    logins.add_argument("--email", action="append", metavar="ADDRESS", help="only this demo staff member (repeatable)")
    logins.add_argument(
        "--reset-passwords",
        "--reset-password",
        action="store_true",
        help="issue a new password to accounts that already exist",
    )
    logins.add_argument(
        "--temporary-password",
        action="store_true",
        help="make Keycloak ask for a new password at first sign-in (default: no; a demo has no time for it)",
    )
    logins.add_argument(
        "--judge", action="store_true", help="public judge only: env password, no TOTP; no --out needed"
    )
    logins.set_defaults(func=cmd_demo_logins)
    dev = sub.add_parser("dev-account", help="dev/test only: fresh Keycloak login for an existing staff user")
    dev.add_argument("--email", required=True)
    dev.set_defaults(func=cmd_dev_account)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
