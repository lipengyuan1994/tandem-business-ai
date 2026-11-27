"""P2-04: staff, roles, invitations; role matrix sanity; memberships isolation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, text

from app.modules.identity.roles import ROLES
from app.modules.identity.testing import add_member, login_as, make_user
from app.platform.context import Context
from app.platform.db import transaction

from .conftest import FakeKeycloak, make_business


def _members(client: Any) -> dict[str, Any]:
    return {m["email"]: m for m in client.get("/api/v1/identity/members").json()}


def test_cannot_remove_last_owner(app: Any) -> None:
    business = make_business("example-cycle-works")
    owner_id = make_user("morgan@example.com")
    owner_membership = add_member(owner_id, business, ["owner"])
    owner = login_as(app, business, ["owner"], user_id=owner_id)
    me = _members(owner)["morgan@example.com"]
    response = owner.put(f"/api/v1/identity/members/{owner_membership}/roles", json={"roles": ["admin"]})
    assert response.status_code == 409 and response.json()["code"] == "identity.last_owner"
    response = owner.patch(
        f"/api/v1/identity/members/{owner_membership}", json={"version": me["version"], "status": "suspended"}
    )
    assert response.status_code == 409


def test_admin_cannot_grant_owner(app: Any) -> None:
    business = make_business("example-cycle-works")
    admin = login_as(app, business, ["admin"])
    target = add_member(make_user("casey@example.com"), business, ["operator"])
    response = admin.put(f"/api/v1/identity/members/{target}/roles", json={"roles": ["owner"]})
    assert response.status_code == 403 and response.json()["code"] == "identity.owner_grant_forbidden"
    assert (
        admin.put(
            f"/api/v1/identity/members/{target}/roles", json={"roles": ["operator", "inventory_manager"]}
        ).status_code
        == 200
    )


def test_invite_existing_user_adds_membership_immediately(app: Any, keycloak: FakeKeycloak) -> None:
    cycle, garden = make_business("example-cycle-works"), make_business("example-garden-store")
    sam = make_user("sam@example.com")
    add_member(sam, garden, ["owner"])
    owner = login_as(app, cycle, ["owner"])
    response = owner.post(
        "/api/v1/identity/invitations", json={"email": "sam@example.com", "display_name": "Sam", "roles": ["auditor"]}
    )
    assert (
        response.status_code == 201
        and response.json()["status"] == "accepted"
        and response.json()["temporary_password"] is None
    )
    assert keycloak.created_users == []
    assert _members(owner)["sam@example.com"]["roles"] == ["auditor"]
    again = owner.post(
        "/api/v1/identity/invitations", json={"email": "sam@example.com", "display_name": "Sam", "roles": ["auditor"]}
    )
    assert again.status_code == 409


def test_operator_cannot_manage_staff(app: Any) -> None:
    business = make_business("example-cycle-works")
    operator = login_as(app, business, ["operator"])
    assert operator.get("/api/v1/identity/members").status_code == 403
    assert (
        operator.post(
            "/api/v1/identity/invitations", json={"email": "x@example.com", "display_name": "X", "roles": ["operator"]}
        ).status_code
        == 403
    )


def test_members_are_isolated_between_businesses(app: Any) -> None:
    cycle, garden = make_business("example-cycle-works"), make_business("example-garden-store")
    add_member(make_user("gwen@example.com"), garden, ["operator"])
    garden_membership = add_member(make_user("hal@example.com"), garden, ["operator"])
    owner = login_as(app, cycle, ["owner"])
    assert "gwen@example.com" not in _members(owner)
    response = owner.put(f"/api/v1/identity/members/{garden_membership}/roles", json={"roles": ["admin"]})
    assert response.status_code == 404


def test_user_sees_own_memberships_only_across_businesses() -> None:
    cycle, garden = make_business("example-cycle-works"), make_business("example-garden-store")
    morgan, sam = make_user("morgan@example.com"), make_user("sam@example.com")
    add_member(morgan, cycle, ["owner"])
    add_member(sam, garden, ["owner"])
    with transaction(Context(request_id="t", actor_kind="user", user_id=morgan, business_id=None)) as db:
        rows = db.execute(text("select business_id from memberships")).scalars().all()
        names = db.execute(text("select slug from businesses")).scalars().all()
    assert rows == [cycle] and names == ["example-cycle-works"]


def test_role_matrix_sanity() -> None:
    writes = {p for p in ROLES["auditor"] if p.endswith((".write", ".adjust", ".approve", ".create"))}
    assert writes == set()
    assert "inventory.stock.adjust" not in ROLES["operator"]
    assert "marketing.proposal.approve" not in ROLES["operator"]
    assert {"identity.owner.grant", "org.business.admin"} - ROLES["admin"] == {
        "identity.owner.grant",
        "org.business.admin",
    }
    assert ROLES["admin"] < ROLES["owner"]


def test_demo_reviewer_has_exactly_admin_permissions_except_configuration() -> None:
    excluded = {
        "org.settings.write",
        "org.business.write",
        "org.location.write",
        "identity.membership.write",
        "payments.connection.manage",
    }
    forbidden = excluded | {
        "ai.settings.write",
        "identity.owner.grant",
        "audit.export",
        "admin_ops.data.manage",
        "org.business.admin",
    }
    assert ROLES["demo_reviewer"] == ROLES["admin"] - excluded
    assert not (ROLES["demo_reviewer"] & forbidden)


def test_demo_reviewer_can_read_staff_but_cannot_change_roles_or_invite(app: Any) -> None:
    business = make_business("example-cycle-works")
    judge = login_as(app, business, ["demo_reviewer"])
    target = add_member(make_user("riley@example.com"), business, ["admin"])
    assert judge.get("/api/v1/identity/members").status_code == 200
    assert judge.put(f"/api/v1/identity/members/{target}/roles", json={"roles": ["owner"]}).status_code == 403
    assert (
        judge.patch(f"/api/v1/identity/members/{target}", json={"version": 1, "status": "suspended"}).status_code == 403
    )
    assert (
        judge.post(
            "/api/v1/identity/invitations", json={"email": "x@example.com", "display_name": "X", "roles": ["admin"]}
        ).status_code
        == 403
    )


def test_demo_reviewer_cannot_mutate_business_ai_or_autonomy_settings(app: Any) -> None:
    business = make_business("example-cycle-works")
    judge = login_as(app, business, ["demo_reviewer"])
    assert judge.get("/api/v1/org/business").status_code == 200
    settings = judge.get("/api/v1/org/settings").json()
    assert judge.patch("/api/v1/org/settings", json=settings).status_code == 403
    assert judge.patch("/api/v1/org/business", json={"version": 1, "name": "Changed"}).status_code == 403
    assert judge.post("/api/v1/org/locations", json={"name": "Changed", "timezone": "UTC"}).status_code == 403
    assert judge.patch("/api/v1/ai/agents/front_desk", json={"version": 1, "enabled": False}).status_code == 403
    assert judge.put("/api/v1/ai/autonomy/rules", json={}).status_code == 403
    assert judge.post("/api/v1/ai/autonomy/pause").status_code == 403
    assert judge.post("/api/v1/ai/autonomy/resume").status_code == 403


def test_pending_invitations_are_listed_and_can_be_revoked(app: Any, keycloak: FakeKeycloak) -> None:
    cycle, garden = make_business("example-cycle-works"), make_business("example-garden-store")
    owner = login_as(app, cycle, ["owner"])
    created = owner.post(
        "/api/v1/identity/invitations", json={"email": "Nia@Example.com", "display_name": "Nia", "roles": ["operator"]}
    ).json()
    listed = owner.get("/api/v1/identity/invitations").json()
    assert [(i["id"], i["email"], i["roles"]) for i in listed] == [(created["id"], "nia@example.com", ["operator"])]
    assert all(i["temporary_password"] is None for i in listed)
    assert login_as(app, garden, ["owner"]).get("/api/v1/identity/invitations").json() == []
    assert login_as(app, cycle, ["operator"]).get("/api/v1/identity/invitations").status_code == 403
    assert (
        login_as(app, cycle, ["auditor"]).post(f"/api/v1/identity/invitations/{created['id']}/revoke").status_code
        == 403
    )
    assert owner.post(f"/api/v1/identity/invitations/{created['id']}/revoke").status_code == 204
    assert owner.get("/api/v1/identity/invitations").json() == []
    assert owner.post(f"/api/v1/identity/invitations/{created['id']}/revoke").status_code == 409


def test_expired_invitation_can_be_sent_again(app: Any, keycloak: FakeKeycloak, owner_engine: Engine) -> None:
    business = make_business("example-cycle-works")
    owner = login_as(app, business, ["owner"])
    body = {"email": "nia@example.com", "display_name": "Nia", "roles": ["operator"]}
    first = owner.post("/api/v1/identity/invitations", json=body)
    assert first.status_code == 201, first.text
    assert owner.post("/api/v1/identity/invitations", json=body).json()["code"] == "identity.invitation_exists"

    with owner_engine.begin() as conn:  # a week passes
        conn.execute(text("update invitations set expires_at = now() - interval '1 day'"))
    assert owner.get("/api/v1/identity/invitations").json() == []  # nothing left to revoke
    again = owner.post("/api/v1/identity/invitations", json=body)
    assert again.status_code == 201, again.text
    # the account from the first invitation is reused; a fresh password would not be set on it, so none is shown
    assert again.json()["temporary_password"] is None
    assert len(keycloak.created_users) == 1
    assert [i["id"] for i in owner.get("/api/v1/identity/invitations").json()] == [again.json()["id"]]
    with owner_engine.connect() as conn:
        statuses = conn.execute(text("select status from invitations order by expires_at")).scalars().all()
    assert statuses == ["expired", "pending"]


def test_removed_member_can_be_invited_again(app: Any, keycloak: FakeKeycloak) -> None:
    """Removing someone used to make every later invitation fail with "already a member"."""
    business = make_business("example-cycle-works")
    owner = login_as(app, business, ["owner"])
    casey = make_user("casey@example.com")
    membership = add_member(casey, business, ["admin"])
    me = _members(owner)["casey@example.com"]
    removed = owner.patch(
        f"/api/v1/identity/members/{membership}", json={"version": me["version"], "status": "removed"}
    )
    assert removed.status_code == 200, removed.text  # used to be a 500 that rolled the removal back
    assert removed.json()["status"] == "removed"
    assert "casey@example.com" not in _members(owner)

    body = {"email": "casey@example.com", "display_name": "Casey", "roles": ["operator"]}
    again = owner.post("/api/v1/identity/invitations", json=body)
    assert again.status_code == 201, again.text
    assert again.json()["status"] == "accepted"
    back = _members(owner)["casey@example.com"]
    # same membership, active again, with only the invited role (the earlier admin role doesn't come back)
    assert back["membership_id"] == str(membership)
    assert back["status"] == "active" and back["roles"] == ["operator"]
    assert owner.post("/api/v1/identity/invitations", json=body).json()["code"] == "identity.already_member"


def test_reinvited_member_does_not_keep_old_job_title(app: Any, keycloak: FakeKeycloak) -> None:
    """A returning staff member used to come back with the job title they had before the removal."""
    business = make_business("example-cycle-works")
    owner = login_as(app, business, ["owner"])
    membership = add_member(make_user("casey@example.com"), business, ["admin"])
    me = _members(owner)["casey@example.com"]
    titled = owner.patch(f"/api/v1/identity/members/{membership}", json={"version": me["version"], "title": "Manager"})
    assert titled.status_code == 200, titled.text
    removed = owner.patch(
        f"/api/v1/identity/members/{membership}", json={"version": titled.json()["version"], "status": "removed"}
    )
    assert removed.status_code == 200, removed.text

    body = {"email": "casey@example.com", "display_name": "Casey", "roles": ["operator"]}
    assert owner.post("/api/v1/identity/invitations", json=body).status_code == 201
    back = _members(owner)["casey@example.com"]
    assert back["status"] == "active" and back["title"] is None
