"""Staff can copy a pending supplier's quote link: the same capability the e-mail carries, audited, never logged."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.registry import Registry

from ..schemas_quotes import CreatePOFromQuote
from .test_purchasing import BASE
from .test_quotes import execute, form, record, request_quotes, second_supplier

PARTS = "parts@example.com"


def link_path(request_id: UUID, supplier_id: str) -> str:
    return f"{BASE}/quote-requests/{request_id}/invites/{supplier_id}/link"


def copy_link(shop: Any, request_id: UUID, supplier_id: str) -> Any:
    return shop["client"].post(link_path(request_id, supplier_id))


def email_url(owner_engine: Engine, recipient: str) -> str:
    """The link exactly as the queued e-mail carries it (HTTP never shows it)."""
    with owner_engine.connect() as db:
        return str(
            db.scalar(
                text(
                    "select variables->>'quote_url' from outbox_messages "
                    "where recipient = :r and template_key = 'quote_request'"
                ),
                {"r": recipient},
            )
        )


def audit_rows(owner_engine: Engine) -> list[Any]:
    with owner_engine.connect() as db:
        sql = text("select * from audit_events where action = 'purchasing.quote_link.copied'")
        return list(db.execute(sql).mappings())


def third_supplier(shop: Any) -> str:
    result = shop["client"].post(BASE + "/suppliers", json={"name": "Example Third", "email": "third@example.com"})
    assert result.status_code == 201, result.text
    return str(result.json()["id"])


def test_copied_link_is_the_one_the_email_carries_and_the_supplier_page_accepts_it(
    shop: Any, registry: Registry, owner_engine: Engine
) -> None:
    request_id = request_quotes(shop, registry, [shop["supplier"]["id"], second_supplier(shop)])
    response = copy_link(shop, request_id, shop["supplier"]["id"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    url = response.json()["url"]
    assert url == email_url(owner_engine, PARTS)
    assert url.startswith("https://") and "/q/" in url
    assert copy_link(shop, request_id, shop["supplier"]["id"]).json() == {"url": url}  # the same link every time
    assert form(shop).get("/api/v1/public/quotes/" + url.rsplit("/", 1)[1]).status_code == 200


@pytest.mark.parametrize(
    ("role", "allowed"),
    [("owner", True), ("admin", True), ("inventory_manager", True), ("operator", False), ("auditor", False)],
)
def test_only_roles_with_purchasing_write_may_copy_a_link(
    shop: Any, registry: Registry, role: str, allowed: bool
) -> None:
    request_id = request_quotes(shop, registry)
    client = login_as(shop["app"], shop["business"], [role])
    response = client.post(link_path(request_id, shop["supplier"]["id"]))
    assert response.status_code == (200 if allowed else 403), response.text


def test_other_businesses_and_unknown_rows_are_not_found(shop: Any, registry: Registry) -> None:
    request_id = request_quotes(shop, registry)
    stranger = login_as(shop["app"], make_business("example-garden-store"), ["owner"])
    assert stranger.post(link_path(request_id, shop["supplier"]["id"])).status_code == 404
    assert copy_link(shop, request_id, second_supplier(shop)).status_code == 404  # a supplier that was never invited
    assert copy_link(shop, UUID(int=1), shop["supplier"]["id"]).status_code == 404


def test_answered_declined_and_expired_invites_are_refused(shop: Any, registry: Registry) -> None:
    pending, declining = second_supplier(shop), third_supplier(shop)
    request_id = request_quotes(shop, registry, [shop["supplier"]["id"], pending, declining])
    declining_token = copy_link(shop, request_id, declining).json()["url"].rsplit("/", 1)[1]
    assert copy_link(shop, request_id, shop["supplier"]["id"]).status_code == 200

    record(shop, registry, request_id)  # the first supplier answers
    answered = copy_link(shop, request_id, shop["supplier"]["id"])
    assert answered.status_code == 409 and answered.json()["code"] == "purchasing.invite_not_unanswered"

    declined = form(shop).post("/api/v1/public/quotes/" + declining_token, json={"declined": True})
    assert declined.status_code == 200
    assert copy_link(shop, request_id, declining).json()["code"] == "purchasing.invite_not_unanswered"

    assert copy_link(shop, request_id, pending).status_code == 200  # still waiting
    clock.set_clock(lambda: datetime(2026, 11, 7, 7, tzinfo=UTC))  # past the last valid local day (reply_by + 7)
    later = login_as(shop["app"], shop["business"], ["owner"])  # the earlier staff session has timed out by now
    expired = later.post(link_path(request_id, pending))
    assert expired.status_code == 409 and expired.json()["code"] == "purchasing.invite_not_unanswered"


def test_a_closed_request_has_no_links(shop: Any, registry: Registry) -> None:
    request_id = request_quotes(shop, registry, [shop["supplier"]["id"], second_supplier(shop)])
    quote_id = record(shop, registry, request_id)
    execute(shop, registry, "create_po_from_quote", CreatePOFromQuote(quote_id=quote_id, quote_version=1))
    closed = copy_link(shop, request_id, shop["supplier"]["id"])
    assert closed.status_code == 409 and closed.json()["code"] == "purchasing.quote_request_closed"


def test_a_link_that_cannot_be_rebuilt_is_refused_instead_of_guessed(
    shop: Any, registry: Registry, owner_engine: Engine
) -> None:
    request_id = request_quotes(shop, registry)
    with owner_engine.begin() as db:  # as if APP_SECRET_KEY or the business time zone changed since sending
        db.execute(text("update quote_request_suppliers set token_hash = repeat('0', 64)"))
    refused = copy_link(shop, request_id, shop["supplier"]["id"])
    assert refused.status_code == 409 and refused.json()["code"] == "purchasing.quote_link_unavailable"
    assert audit_rows(owner_engine) == []


def test_copying_is_audited_without_the_token_and_nothing_is_logged(
    shop: Any,
    registry: Registry,
    owner_engine: Engine,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    request_id = request_quotes(shop, registry)
    caplog.set_level("DEBUG")
    url = copy_link(shop, request_id, shop["supplier"]["id"]).json()["url"]
    token = url.rsplit("/", 1)[1]
    rows = audit_rows(owner_engine)
    assert len(rows) == 1
    assert (rows[0]["target_type"], rows[0]["target_id"], rows[0]["actor_kind"]) == (
        "quote_request",
        request_id,
        "user",
    )
    assert rows[0]["changes"] == {"supplier_id": shop["supplier"]["id"]}
    with owner_engine.connect() as db:
        everything = str([dict(r) for r in db.execute(text("select * from audit_events")).mappings()])
    out, err = capfd.readouterr()
    for place in (everything, caplog.text, out, err):
        assert token not in place and url not in place
