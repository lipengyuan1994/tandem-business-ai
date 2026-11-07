"""P2-04: staff, roles, invitations; role matrix sanity; memberships isolation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

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
    excluded = {"org.settings.write", "org.business.write", "org.location.write", "identity.membership.write"}
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
