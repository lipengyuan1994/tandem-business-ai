"""Add the public judge to existing seeded businesses without resetting any demo state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.cli import main
from app.modules.identity import keycloak_admin
from app.modules.identity.testing import add_member, make_user
from app.modules.org.testing import make_business
from app.platform import clock
from app.seed import DEFAULT_FIXTURES, seed

from .test_demo_logins import THURSDAY, FakeKeycloak
from .test_demo_logins import keycloak as keycloak
from .test_demo_logins import seeded as seeded

pytestmark = pytest.mark.integration


@pytest.fixture
def judge_cli(keycloak: FakeKeycloak, monkeypatch: pytest.MonkeyPatch) -> FakeKeycloak:
    monkeypatch.setenv("DEMO_JUDGE_PASSWORD", "Public-judge-test-2026")
    monkeypatch.setattr(keycloak_admin, "harden_judge_user", lambda uid: None)
    monkeypatch.setattr(keycloak_admin, "is_legacy_demo_guest", lambda uid, email: keycloak.find(email) == uid)
    monkeypatch.setattr(
        keycloak_admin, "reset_judge_password", lambda uid, pw: keycloak.set_password(uid, pw, temporary=False)
    )
    return keycloak


@pytest.fixture
def seeded_without_judge(registry: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing install's fixtures, before P10-J added the judge; no destructive reset in the command."""
    clock.set_clock(lambda: THURSDAY)
    doc = json.loads(DEFAULT_FIXTURES.read_text())
    tables = doc["tables"]
    judge_ids = {u["id"] for u in tables["users"] if u["email"] == "demo.guest@example.com"}
    memberships = {m["id"] for m in tables["memberships"] if m["user_id"] in judge_ids}
    tables["users"] = [u for u in tables["users"] if u["id"] not in judge_ids]
    tables["memberships"] = [m for m in tables["memberships"] if m["id"] not in memberships]
    tables["role_grants"] = [g for g in tables["role_grants"] if g["membership_id"] not in memberships]
    path = tmp_path / "pre-judge-fixtures.json"
    path.write_text(json.dumps(doc))
    seed(path)

    def forbid_seed(*args: Any, **kwargs: Any) -> None:
        pytest.fail("judge login must not seed or reset an existing demo")

    monkeypatch.setattr("app.seed.seed", forbid_seed)


def _database_snapshot(engine: Engine, *, without_judge: bool = False) -> dict[str, str]:
    """Hash full rows, including ids/versions/timestamps, without dumping synthetic tokens into assertion output."""
    excluded = {
        "users": "email <> 'demo.guest@example.com'",
        "memberships": "user_id not in (select id from users where email='demo.guest@example.com')",
        "role_grants": "membership_id not in (select m.id from memberships m join users u on u.id=m.user_id "
        "where u.email='demo.guest@example.com')",
    }
    with engine.connect() as db:
        tables = db.execute(
            text("select tablename from pg_tables where schemaname='public' order by tablename")
        ).scalars()
        result: dict[str, str] = {}
        for table in tables:
            quoted = db.dialect.identifier_preparer.quote(table)
            where = " where " + excluded[table] if without_judge and table in excluded else ""
            result[table] = db.execute(
                text(
                    "select md5(coalesce(jsonb_agg(to_jsonb(r) order by to_jsonb(r)::text)::text,'[]')) "  # noqa: S608 -- catalog identifiers quoted above; fixed predicates
                    f"from {quoted} r{where}"
                )
            ).scalar_one()
        return result


def test_judge_creates_user_and_three_memberships_on_existing_install_without_reset(
    seeded_without_judge: None,
    judge_cli: FakeKeycloak,
    owner_engine: Engine,
) -> None:
    from app.platform.ids import fixture_id

    main(["demo-logins", "--judge", "--reset-password"])
    with owner_engine.connect() as db:
        user = db.execute(
            text("select id,display_name,oidc_subject,status from users where email='demo.guest@example.com'")
        ).one()
        members = db.execute(
            text(
                "select m.business_id,m.status,r.role from memberships m join role_grants r on r.membership_id=m.id "
                "where m.user_id=:u and r.revoked_at is null"
            ),
            {"u": user.id},
        ).all()
    assert (user.display_name, user.oidc_subject, user.status) == ("Demo Guest", "kc-1", "active")
    assert {(m.business_id, m.status, m.role) for m in members} == {
        (fixture_id(key), "active", "demo_reviewer") for key in ("biz-cycle", "biz-garden", "biz-dental")
    }
    assert len(judge_cli.created) == 1 and judge_cli.accounts == {"demo.guest@example.com": "kc-1"}


def test_judge_second_run_does_not_change_any_app_rows_or_reissue_password(
    seeded_without_judge: None,
    judge_cli: FakeKeycloak,
    owner_engine: Engine,
) -> None:
    main(["demo-logins", "--judge"])
    before = _database_snapshot(owner_engine)
    main(["demo-logins", "--judge"])
    assert _database_snapshot(owner_engine) == before
    assert (
        len(judge_cli.created) == 1
        and judge_cli.accounts == {"demo.guest@example.com": "kc-1"}
        and not judge_cli.resets
    )


def test_judge_provision_leaves_other_members_and_all_demo_state_untouched(
    seeded_without_judge: None,
    judge_cli: FakeKeycloak,
    owner_engine: Engine,
) -> None:
    other = make_business("example-other-business")
    add_member(make_user("other@example.com", "Other Example"), other, ["owner"])
    with owner_engine.begin() as db:
        db.execute(
            text(
                'update business_settings set settings=settings || \'{"ai_reply_guardrail": "Video guardrail"}\'::jsonb'
            )
        )
    before = _database_snapshot(owner_engine, without_judge=True)
    main(["demo-logins", "--judge", "--business", "example-cycle-works"])
    assert _database_snapshot(owner_engine, without_judge=True) == before
    with owner_engine.connect() as db:
        assert (
            db.execute(
                text(
                    "select count(*) from memberships m join users u on u.id=m.user_id "
                    "where u.email='demo.guest@example.com'"
                )
            ).scalar_one()
            == 3
        )
    assert len(judge_cli.created) == 1 and judge_cli.accounts == {"demo.guest@example.com": "kc-1"}


def test_judge_fills_missing_membership_and_grant_and_reactivates_only_its_own_membership(
    seeded: None,
    judge_cli: FakeKeycloak,
    owner_engine: Engine,
) -> None:
    from app.platform.ids import fixture_id

    with owner_engine.begin() as db:
        uid = db.execute(text("select id from users where email='demo.guest@example.com'")).scalar_one()
        db.execute(text("update users set oidc_subject='kc-judge' where id=:u"), {"u": uid})
        db.execute(
            text(
                "delete from role_grants where membership_id in (select id from memberships where user_id=:u) "
                "and business_id <> :d"
            ),
            {"u": uid, "d": fixture_id("biz-dental")},
        )
        db.execute(
            text("delete from memberships where user_id=:u and business_id=:b"),
            {"u": uid, "b": fixture_id("biz-garden")},
        )
        db.execute(
            text("update memberships set status='removed' where user_id=:u and business_id=:b"),
            {"u": uid, "b": fixture_id("biz-cycle")},
        )
    judge_cli.accounts["demo.guest@example.com"] = "kc-judge"
    before = _database_snapshot(owner_engine, without_judge=True)
    main(["demo-logins", "--judge"])
    assert _database_snapshot(owner_engine, without_judge=True) == before
    with owner_engine.connect() as db:
        assert db.execute(text("select oidc_subject from users where id=:u"), {"u": uid}).scalar_one() == "kc-judge"
        assert (
            db.execute(
                text(
                    "select count(*) from memberships m join role_grants r on r.membership_id=m.id "
                    "where m.user_id=:u and m.status='active' and r.role='demo_reviewer' "
                    "and r.revoked_at is null"
                ),
                {"u": uid},
            ).scalar_one()
            == 3
        )
    after = _database_snapshot(owner_engine)
    main(["demo-logins", "--judge"])
    assert _database_snapshot(owner_engine) == after
    assert not judge_cli.created and not judge_cli.resets


@pytest.mark.parametrize("wrong_id", [False, True])
def test_judge_validates_every_business_before_creating_app_rows(
    seeded_without_judge: None,
    judge_cli: FakeKeycloak,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    wrong_id: bool,
) -> None:
    from types import SimpleNamespace

    from app import demo_logins
    from app.platform.ids import fixture_id

    resolver = demo_logins.build_registry().public_business_resolver
    assert resolver is not None
    invalid_id = fixture_id("not-a-seeded-demo") if wrong_id else None
    monkeypatch.setattr(
        demo_logins,
        "build_registry",
        lambda: SimpleNamespace(
            public_business_resolver=lambda slug: invalid_id if slug == "example-dental-clinic" else resolver(slug)
        ),
    )
    before = _database_snapshot(owner_engine)
    with pytest.raises(SystemExit, match=r"example-dental-clinic.*Nothing was changed"):
        main(["demo-logins", "--judge", "--business", "example-cycle-works"])
    assert _database_snapshot(owner_engine) == before
    assert not judge_cli.created


@pytest.mark.parametrize("reason", ["regulated", "non-demo"])
def test_judge_scope_refusals_do_not_create_app_rows(
    seeded_without_judge: None,
    judge_cli: FakeKeycloak,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    from app.platform.config import get_settings

    args = ["demo-logins", "--judge"]
    if reason == "regulated":
        monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    else:
        args += ["--business", "example-other-business"]
    before = _database_snapshot(owner_engine)
    with pytest.raises(SystemExit, match=r"DATA_PROFILE=regulated|not a demo business"):
        main(args)
    assert _database_snapshot(owner_engine) == before
    assert not judge_cli.created


@pytest.mark.parametrize("disabled", [False, True])
def test_judge_does_not_publish_a_disabled_or_privileged_app_account(
    seeded: None,
    judge_cli: FakeKeycloak,
    owner_engine: Engine,
    disabled: bool,
) -> None:
    from app.platform.ids import fixture_id

    with owner_engine.begin() as db:
        uid = db.execute(text("select id from users where email='demo.guest@example.com'")).scalar_one()
        if disabled:
            db.execute(text("update users set status='disabled' where id=:u"), {"u": uid})
        else:
            db.execute(
                text(
                    "update role_grants set role='admin' where business_id=:b and membership_id in "
                    "(select id from memberships where user_id=:u)"
                ),
                {"u": uid, "b": fixture_id("biz-cycle")},
            )
    before = _database_snapshot(owner_engine)
    with pytest.raises(SystemExit, match=r"guest user is disabled|guest has unexpected roles"):
        main(["demo-logins", "--judge"])
    assert _database_snapshot(owner_engine) == before
    assert not judge_cli.created


@pytest.mark.parametrize("linked", [False, True])
def test_legacy_guest_keeps_user_subject_and_memberships_after_two_runs(
    seeded: None, judge_cli: FakeKeycloak, owner_engine: Engine, linked: bool
) -> None:
    old_email = "judge@example.com"
    subject = "original-demo-keycloak-id"
    judge_cli.accounts[old_email] = subject
    with owner_engine.begin() as db:
        uid = db.execute(
            text(
                "update users set email=:old, display_name='Demo Judge', oidc_subject=:sub "
                "where email='demo.guest@example.com' returning id"
            ),
            {"old": old_email, "sub": subject if linked else "pending:" + old_email},
        ).scalar_one()
        business_rows = list(
            db.execute(text("select id,business_id from memberships where user_id=:uid"), {"uid": uid})
        )
    main(["demo-logins", "--guest"])
    before = _database_snapshot(owner_engine)
    main(["demo-logins", "--guest"])
    assert _database_snapshot(owner_engine) == before
    assert judge_cli.accounts == {"demo.guest@example.com": subject}
    assert not judge_cli.created and not judge_cli.resets
    with owner_engine.connect() as db:
        assert tuple(
            db.execute(text("select email,display_name,oidc_subject from users where id=:uid"), {"uid": uid}).one()
        ) == ("demo.guest@example.com", "Demo Guest", subject)
        assert set(db.execute(text("select id,business_id from memberships where user_id=:uid"), {"uid": uid})) == set(
            business_rows
        )


def test_guest_refuses_unlinked_destination_account(
    seeded: None, judge_cli: FakeKeycloak, owner_engine: Engine
) -> None:
    judge_cli.accounts["demo.guest@example.com"] = "somebody-else"
    before = _database_snapshot(owner_engine)
    with pytest.raises(SystemExit, match="not linked"):
        main(["demo-logins", "--guest"])
    assert _database_snapshot(owner_engine) == before
    assert not judge_cli.created and not judge_cli.resets


def test_guest_retries_interrupted_keycloak_rename(
    seeded: None, judge_cli: FakeKeycloak, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_subject: str, _email: str) -> None:
        raise RuntimeError("interrupted before rename")

    with monkeypatch.context() as interrupted:
        interrupted.setattr(keycloak_admin, "rename_public_guest", fail)
        with pytest.raises(RuntimeError, match="interrupted"):
            main(["demo-logins", "--guest"])
    monkeypatch.setenv("DEMO_GUEST_PASSWORD", "Rotated-public-2026")
    main(["demo-logins", "--guest", "--reset-password"])
    assert len(judge_cli.created) == 1 and judge_cli.accounts == {"demo.guest@example.com": "kc-1"}
    assert judge_cli.resets == [{"id": "kc-1", "password": "Rotated-public-2026", "temporary": False}]


@pytest.mark.parametrize("collision", ["app", "invitation", "outside-membership"])
def test_guest_scope_conflicts_preserve_rows_and_credentials(
    seeded: None, judge_cli: FakeKeycloak, owner_engine: Engine, collision: str
) -> None:
    other = make_business("another-company")
    with owner_engine.begin() as db:
        uid = db.execute(text("select id from users where email='demo.guest@example.com'")).scalar_one()
        if collision == "app":
            db.execute(text("update users set display_name='Different Example' where id=:u"), {"u": uid})
        if collision == "invitation":
            db.execute(
                text(
                    "insert into invitations(id,business_id,email,display_name,roles,status,expires_at) "
                    "values(gen_random_uuid(),:b,'demo.guest@example.com','Another Example',array['admin'],"
                    "'pending',now()+interval '7 days')"
                ),
                {"b": other},
            )
    if collision == "outside-membership":
        add_member(uid, other, ["admin"])
    before = _database_snapshot(owner_engine)
    with pytest.raises(
        SystemExit, match=r"unrecognized demo identity|outstanding invitations|outside the synthetic demo"
    ):
        main(["demo-logins", "--guest"])
    assert _database_snapshot(owner_engine) == before
    assert not judge_cli.created and not judge_cli.resets
