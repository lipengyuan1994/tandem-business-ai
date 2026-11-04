"""`python -m app.cli demo-logins`: Keycloak logins for the demo staff of a production install (app/demo_logins.py).

The real command runs against the seeded demo data and a real database; only Keycloak is replaced, by an in-memory
stand-in that records what the command asked for (`app/modules/identity/tests/test_demo_logins.py` covers the HTTP
requests themselves).
"""

from __future__ import annotations

import json
import logging
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app import demo_logins
from app.cli import main
from app.modules.identity import keycloak_admin
from app.modules.identity.keycloak_admin import KeycloakError
from app.modules.identity.testing import add_member, make_user
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.config import get_settings
from app.seed import DEFAULT_FIXTURES, seed

pytestmark = pytest.mark.integration
THURSDAY = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)


def fixture_emails() -> dict[str, set[str]]:
    """Demo staff by business slug, read straight from data/fixtures.json (not through the code under test)."""
    tables = json.loads(DEFAULT_FIXTURES.read_text(encoding="utf-8"))["tables"]
    emails = {u["id"]: u["email"] for u in tables["users"]}
    slugs = {b["id"]: b["slug"] for b in tables["businesses"]}
    staff: dict[str, set[str]] = {slug: set() for slug in slugs.values()}
    for m in tables["memberships"]:
        staff[slugs[m["business_id"]]].add(emails[m["user_id"]])
    return staff


ALL_DEMO_STAFF = set().union(*fixture_emails().values())


class FakeKeycloak:
    """The admin calls `bootstrap-business` and invitations use, answered from memory."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.accounts: dict[str, str] = {}  # e-mail -> Keycloak id
        self.created: list[dict[str, Any]] = []
        self.resets: list[dict[str, Any]] = []
        self.fail_for: set[str] = set()
        monkeypatch.setattr(keycloak_admin, "find_user_id", self.find)
        monkeypatch.setattr(keycloak_admin, "create_user", self.create)
        monkeypatch.setattr(keycloak_admin, "set_password", self.set_password)

    def find(self, email: str) -> str | None:
        return self.accounts.get(email)

    def create(self, email: str, display_name: str, password: str | None, **options: Any) -> str:
        if email in self.fail_for:
            raise KeycloakError("identity.keycloak_unavailable", detail="create user: HTTP 500")
        self.accounts[email] = f"kc-{len(self.accounts) + 1}"
        self.created.append({"email": email, "name": display_name, "password": password, **options})
        return self.accounts[email]

    def set_password(self, user_id: str, password: str, *, temporary: bool) -> None:
        self.resets.append({"id": user_id, "password": password, "temporary": temporary})

    @property
    def created_emails(self) -> list[str]:
        return [c["email"] for c in self.created]

    @property
    def passwords(self) -> dict[str, str]:
        return {c["email"]: c["password"] for c in self.created} | {r["id"]: r["password"] for r in self.resets}


@pytest.fixture
def keycloak(monkeypatch: pytest.MonkeyPatch) -> FakeKeycloak:
    settings = get_settings()
    monkeypatch.setattr(settings, "oidc_internal_issuer_url", "http://keycloak.test/auth/realms/smb")
    monkeypatch.setattr(settings, "keycloak_admin_client_secret", SecretStr("unit-test"))
    return FakeKeycloak(monkeypatch)


@pytest.fixture
def seeded(registry: Any) -> None:
    clock.set_clock(lambda: THURSDAY)
    seed()


def read_file(path: Path) -> dict[str, str]:
    return demo_logins.read_credentials(path)


def run(out: Path, *args: str) -> None:
    main(["demo-logins", "--out", str(out), *args])


# ------------------------------------------------------------------------------------------ who gets a login
def test_creates_logins_for_exactly_the_demo_staff(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "logins.txt"
    run(out)
    assert sorted(keycloak.created_emails) == sorted(ALL_DEMO_STAFF) and len(keycloak.created_emails) == 8
    # Morgan is on two businesses and has one login
    assert keycloak.created_emails.count("morgan@example.com") == 1
    created = {c["email"]: c for c in keycloak.created}
    assert created["riley@example.com"]["name"] == "Riley Example"
    # a demo has no time for a forced password change; the authenticator app is still required
    assert all(c["temporary"] is False and c["required_actions"] == ["CONFIGURE_TOTP"] for c in keycloak.created)
    assert all(c["existing_ok"] is False for c in keycloak.created)

    shown = capsys.readouterr().out
    assert str(out) in shown and all(email in shown for email in ALL_DEMO_STAFF)
    assert "8 created" in shown


def test_people_outside_the_fixtures_and_other_businesses_get_nothing(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path, owner_engine: Engine
) -> None:
    from app.platform.ids import fixture_id

    cycle = fixture_id("biz-cycle")
    stranger = make_user("owner@acme-cycles.example", "Alex Owner")  # a real person invited to the demo business
    add_member(stranger, cycle, ["owner"])
    customer = make_business("acme-cycles")  # a real customer's business with its own staff
    add_member(make_user("staff@acme-cycles.example", "Sam Staff"), customer, ["operator"])
    run(tmp_path / "logins.txt")
    assert set(keycloak.created_emails) == ALL_DEMO_STAFF
    assert not any("acme" in email for email in keycloak.accounts)


def test_one_business_or_one_person_can_be_chosen(seeded: None, keycloak: FakeKeycloak, tmp_path: Path) -> None:
    run(tmp_path / "garden.txt", "--business", "example-garden-store")
    assert (
        set(keycloak.created_emails)
        == fixture_emails()["example-garden-store"]
        == {"sam@example.com", "morgan@example.com"}
    )
    keycloak.accounts.clear()
    keycloak.created.clear()
    run(tmp_path / "two.txt", "--email", "morgan@example.com", "--email", "RILEY@example.com")
    assert sorted(keycloak.created_emails) == ["morgan@example.com", "riley@example.com"]


# ---------------------------------------------------------------------------------------------- refusals
def test_refuses_a_business_that_is_not_a_demo_business(seeded: None, keycloak: FakeKeycloak, tmp_path: Path) -> None:
    customer = make_business("acme-cycles")
    add_member(make_user("owner@acme-cycles.example"), customer, ["owner"])
    out = tmp_path / "logins.txt"
    with pytest.raises(SystemExit, match="'acme-cycles' is not a demo business"):
        run(out, "--business", "acme-cycles")
    with pytest.raises(SystemExit, match="not a demo business"):
        run(out, "--business", "example-cycle-works", "--business", "acme-cycles")  # one bad name refuses all
    assert keycloak.accounts == {} and not out.exists()


def test_refuses_a_person_who_is_not_demo_staff(seeded: None, keycloak: FakeKeycloak, tmp_path: Path) -> None:
    out = tmp_path / "logins.txt"
    with pytest.raises(SystemExit, match=r"owner@acme-cycles\.example is not demo staff"):
        run(out, "--email", "owner@acme-cycles.example")
    with pytest.raises(SystemExit, match=r"sam@example\.com is not demo staff of example-cycle-works"):
        run(out, "--business", "example-cycle-works", "--email", "sam@example.com")  # Sam works at the other one
    assert keycloak.accounts == {} and not out.exists()


def test_refuses_the_regulated_data_profile(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    out = tmp_path / "logins.txt"
    with pytest.raises(SystemExit, match="DATA_PROFILE=regulated"):
        run(out)
    with pytest.raises(SystemExit, match="DATA_PROFILE=regulated"):
        run(tmp_path / "no-such-folder" / "logins.txt", "--business", "example-garden-store")  # decided first
    assert keycloak.accounts == {} and not out.exists()


def test_refuses_a_business_that_only_borrows_a_demo_slug(
    registry: Any, keycloak: FakeKeycloak, tmp_path: Path, owner_engine: Engine
) -> None:
    """Created by hand (bootstrap-business, an import), not by the seed: its id is not the fixture's."""
    by_hand = make_business("example-cycle-works")
    add_member(make_user("morgan@example.com"), by_hand, ["owner"])
    with pytest.raises(SystemExit, match="example-cycle-works exists but is not the seeded demo business"):
        run(tmp_path / "logins.txt", "--business", "example-cycle-works")
    assert keycloak.accounts == {}


def test_refuses_when_the_demo_data_has_not_been_seeded(registry: Any, keycloak: FakeKeycloak, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"example-cycle-works does not exist; run `seed` first"):
        run(tmp_path / "logins.txt")
    assert keycloak.accounts == {}


def test_refuses_fixtures_that_are_not_synthetic_example_com_data(tmp_path: Path) -> None:
    doc = json.loads(DEFAULT_FIXTURES.read_text(encoding="utf-8"))
    bad = tmp_path / "fixtures.json"
    doc["synthetic"] = False
    bad.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SystemExit, match="not marked synthetic"):
        demo_logins.load_demo_businesses(bad)
    doc["synthetic"] = True
    doc["tables"]["users"][0]["email"] = "real.person@gmail.com"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SystemExit, match=r"not an example\.com address"):
        demo_logins.load_demo_businesses(bad)


def test_refuses_before_creating_anything_when_the_passwords_have_nowhere_to_go(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit, match="does not exist"):
        run(tmp_path / "missing-folder" / "logins.txt")  # a container without the mounted folder
    with pytest.raises(SystemExit, match="is a directory"):
        run(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        if os.access(locked, os.W_OK):  # running as root: permissions do not apply
            pytest.skip("directory permissions are not enforced for this user")
        with pytest.raises(SystemExit, match="cannot create files"):
            run(locked / "logins.txt")
    finally:
        locked.chmod(0o700)
    assert keycloak.accounts == {}


def test_refuses_when_keycloak_admin_access_is_not_configured(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "keycloak_admin_client_secret", SecretStr(""))
    with pytest.raises(SystemExit, match="Keycloak admin access is not configured"):
        run(tmp_path / "logins.txt")


def test_it_is_not_a_development_only_command(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dev-account` refuses production; this one exists for production installs."""
    monkeypatch.setattr(get_settings(), "app_env", "prod")
    run(tmp_path / "logins.txt")
    assert set(keycloak.created_emails) == ALL_DEMO_STAFF


# ------------------------------------------------------------------------------------ passwords and the file
def test_passwords_go_to_a_mode_600_file_and_nowhere_else(
    seeded: None,
    keycloak: FakeKeycloak,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    out = tmp_path / "logins.txt"
    run(out)
    run(out, "--reset-passwords")  # the second way a password is issued
    passwords = [c["password"] for c in keycloak.created] + [r["password"] for r in keycloak.resets]
    assert len(passwords) == 16 and all(len(p) >= 16 for p in passwords)

    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    stored = read_file(out)
    assert sorted(stored) == sorted(ALL_DEMO_STAFF)
    assert sorted(stored.values()) == sorted(r["password"] for r in keycloak.resets)  # the file holds the latest

    shown = capsys.readouterr()
    for password in passwords:
        assert password not in shown.out and password not in shown.err
        assert password not in caplog.text
    assert not any(p.name.startswith(".logins") for p in tmp_path.iterdir())  # no temporary file is left behind


def test_the_file_is_mode_600_whatever_the_umask_and_whatever_it_was_before(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path
) -> None:
    out = tmp_path / "logins.txt"
    out.write_text("old content\n")
    out.chmod(0o644)
    previous = os.umask(0o000)
    try:
        run(out)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert set(read_file(out)) == ALL_DEMO_STAFF


def test_a_symbolic_link_at_the_path_is_replaced_not_followed(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path
) -> None:
    target = tmp_path / "somewhere-else.txt"
    target.write_text("do not touch\n")
    out = tmp_path / "logins.txt"
    out.symlink_to(target)
    run(out)
    assert target.read_text() == "do not touch\n" and not out.is_symlink() and set(read_file(out)) == ALL_DEMO_STAFF


def test_the_file_helpers_round_trip_and_ignore_comments(tmp_path: Path) -> None:
    out = tmp_path / "logins.txt"
    assert demo_logins.read_credentials(out) == {}
    demo_logins.write_credentials(out, {"b@example.com": "Pw-b", "a@example.com": "Pw-a"})
    text_ = out.read_text()
    assert text_.startswith("# ") and text_.index("a@example.com") < text_.index("b@example.com")
    assert demo_logins.read_credentials(out) == {"a@example.com": "Pw-a", "b@example.com": "Pw-b"}
    out.write_text(text_ + "\nnot a login line\n# c@example.com Pw-c\n")
    assert demo_logins.read_credentials(out) == {"a@example.com": "Pw-a", "b@example.com": "Pw-b"}


def test_a_failed_write_leaves_the_earlier_file_and_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "logins.txt"
    demo_logins.write_credentials(out, {"a@example.com": "Pw-a"})

    def refuse(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        demo_logins.write_credentials(out, {"a@example.com": "Pw-a", "b@example.com": "Pw-b"})
    assert demo_logins.read_credentials(out) == {"a@example.com": "Pw-a"}
    assert [p.name for p in tmp_path.iterdir()] == ["logins.txt"]


def test_refuses_fixtures_without_any_demo_business(tmp_path: Path) -> None:
    empty = tmp_path / "fixtures.json"
    empty.write_text(
        json.dumps({"synthetic": True, "tables": {"businesses": [], "users": [], "memberships": []}}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="no demo staff selected"):
        demo_logins.run(tmp_path / "logins.txt", fixtures=empty)


# ------------------------------------------------------------------------------------------------ re-runs
def test_a_second_run_changes_nothing(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "logins.txt"
    run(out)
    first_file, first_calls = out.read_bytes(), len(keycloak.created)
    capsys.readouterr()
    run(out)
    shown = capsys.readouterr().out
    assert len(keycloak.created) == first_calls and keycloak.resets == []
    assert out.read_bytes() == first_file
    assert "0 created, 0 password reset, 8 already existed" in shown and "unchanged" in shown
    assert "--reset-passwords" in shown


def test_a_second_run_without_a_file_does_not_create_an_empty_one(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for email in ALL_DEMO_STAFF:  # logins made some other way (the Keycloak console, an invitation)
        keycloak.accounts[email] = "kc-" + email
    out = tmp_path / "logins.txt"
    run(out)
    assert not out.exists() and keycloak.created == [] and "not created" in capsys.readouterr().out


def test_reset_passwords_changes_only_the_chosen_people_and_keeps_the_other_lines(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "logins.txt"
    run(out)
    before = read_file(out)
    capsys.readouterr()
    run(out, "--email", "riley@example.com", "--reset-passwords")
    after = read_file(out)
    assert [r["id"] for r in keycloak.resets] == [keycloak.accounts["riley@example.com"]]
    assert after["riley@example.com"] != before["riley@example.com"]
    assert {e: p for e, p in after.items() if e != "riley@example.com"} == {
        e: p for e, p in before.items() if e != "riley@example.com"
    }
    assert keycloak.resets[0]["temporary"] is False
    assert "password reset: riley@example.com" in capsys.readouterr().out and stat.S_IMODE(out.stat().st_mode) == 0o600


def test_the_temporary_password_flag_is_passed_to_keycloak(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path
) -> None:
    run(tmp_path / "logins.txt", "--email", "riley@example.com", "--temporary-password")
    (created,) = keycloak.created
    assert created["temporary"] is True and created["required_actions"] == ["UPDATE_PASSWORD", "CONFIGURE_TOTP"]
    run(tmp_path / "logins.txt", "--email", "riley@example.com", "--reset-passwords", "--temporary-password")
    assert keycloak.resets[0]["temporary"] is True


# ---------------------------------------------------------------------------------------- partial outcomes
def test_a_failure_part_way_keeps_the_passwords_already_issued(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    keycloak.fail_for.add("quinn@example.com")  # Keycloak starts failing at some account
    out = tmp_path / "logins.txt"
    with pytest.raises(SystemExit, match=r"stopped: identity.keycloak_unavailable \(create user: HTTP 500\)"):
        run(out)
    done = keycloak.created_emails
    assert done and "quinn@example.com" not in done and len(done) < 8
    assert set(read_file(out)) == set(done)  # every account that exists has its password on file
    assert all(read_file(out)[e] == keycloak.passwords[e] for e in done)
    shown = capsys.readouterr().out
    assert all(e in shown for e in done) and all(p not in shown for p in keycloak.passwords.values())
    # run again once Keycloak is back: only the missing accounts are created, the file keeps the earlier lines
    keycloak.fail_for.clear()
    run(out)
    assert set(read_file(out)) == ALL_DEMO_STAFF and len(keycloak.created) == 8


def test_a_person_who_cannot_sign_in_is_reported_and_the_status_is_not_success(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path, owner_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Morgan signed in once (the application is bound to a Keycloak id) but that account is gone."""
    with owner_engine.begin() as conn:
        conn.execute(text("update users set oidc_subject = 'kc-gone' where email = 'morgan@example.com'"))
    with pytest.raises(SystemExit) as stop:
        run(tmp_path / "logins.txt")
    assert stop.value.code == 1
    shown = capsys.readouterr().out
    assert "not usable: morgan@example.com: signed in before, but its Keycloak account no longer exists" in shown
    assert "morgan@example.com" not in keycloak.created_emails and len(keycloak.created_emails) == 7


def test_a_disabled_user_and_a_removed_member_are_skipped_with_a_reason(
    seeded: None, keycloak: FakeKeycloak, tmp_path: Path, owner_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("update users set status = 'disabled' where email = 'jordan@example.com'"))
        conn.execute(
            text(
                "update memberships set status = 'removed' where user_id = (select id from users"
                " where email = 'casey@example.com')"
            )
        )
    run(tmp_path / "logins.txt")
    shown = capsys.readouterr().out
    assert "skipped: jordan@example.com: the user is disabled" in shown
    assert "skipped: casey@example.com: not an active member of example-cycle-works" in shown
    assert set(keycloak.created_emails) == ALL_DEMO_STAFF - {"jordan@example.com", "casey@example.com"}
