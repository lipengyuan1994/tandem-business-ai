"""Every path that can remove an active owner uses the same business lock."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.modules.identity import repository, service
from app.modules.identity.roles import permissions_for
from app.modules.identity.schemas import MemberUpdate
from app.modules.identity.testing import add_member, make_user
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict


@pytest.mark.concurrency
@pytest.mark.parametrize("actions", [("roles", "roles"), ("status", "status"), ("roles", "status")])
def test_concurrent_owner_removals_preserve_one_active_owner(
    business: UUID, monkeypatch: pytest.MonkeyPatch, actions: tuple[str, str]
) -> None:
    users = [make_user(), make_user()]
    members = [add_member(user, business, ["owner"]) for user in users]
    ctx = Context(
        request_id="owner-race",
        actor_kind="user",
        user_id=users[0],
        business_id=business,
        permissions=permissions_for({"owner"}),
    )
    counted_first, counted_second = Event(), Event()
    original = repository.count_active_owners

    def count(db: Session, context: Context) -> int:
        result = original(db, context)
        if not counted_first.is_set():
            counted_first.set()
            # Without serialization both requests see two owners before either mutates.
            counted_second.wait(1)
        else:
            counted_second.set()
        return result

    monkeypatch.setattr(repository, "count_active_owners", count)

    def remove(index: int) -> str:
        try:
            with transaction(ctx) as db:
                if actions[index] == "roles":
                    service.set_roles(db, ctx, members[index], {"admin"})
                else:
                    service.update_member(db, ctx, members[index], MemberUpdate(version=1, status="suspended"))
            return "removed"
        except Conflict as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(remove, 0)
        assert counted_first.wait(5)
        second = pool.submit(remove, 1)
        results = [first.result(timeout=10), second.result(timeout=10)]
    assert sorted(results) == ["identity.last_owner", "removed"]
    with transaction(ctx) as db:
        assert original(db, ctx) == 1
