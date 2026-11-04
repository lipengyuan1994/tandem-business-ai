"""AI-11 briefing: per-user/hour cache, permission-filtered facts, numeric validation."""

from datetime import timedelta

import pytest
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
from app.modules.ai.schemas import Briefing
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.ai.tests.tool_helpers import seed_demo
from app.modules.ai.usecases.briefing import validate_numbers
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.registry import Registry, set_registry
from app.platform.testing.auth import client_for


@pytest.fixture(autouse=True)
def clean_router():
    reset_router_cache()
    yield
    reset_router_cache()


def test_briefing_cached_one_hour_per_user(registry: Registry, owner_engine: Engine) -> None:
    biz = make_business()
    first = Briefing(bullets=["Review today's work."])
    second = Briefing(bullets=["Review open conversations."])
    gw = FakeGateway([first.model_dump_json(), second.model_dump_json(), first.model_dump_json()])
    set_router_for_tests(fake_router_by_class(writer=gw))
    app = create_app(registry)
    perms = {"ai.assistant.use", "org.business.read"}
    a, b = client_for(app, registry, biz, perms), client_for(app, registry, biz, perms)
    response = a.get("/api/v1/ai/briefing")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"generated_at", "bullets"} and body["bullets"] == first.bullets
    assert a.get("/api/v1/ai/briefing").json() == body and len(gw.calls) == 1
    assert b.get("/api/v1/ai/briefing").json()["bullets"] == second.bullets and len(gw.calls) == 2
    later = clock.now() + timedelta(hours=1)
    clock.set_clock(lambda: later)
    assert a.get("/api/v1/ai/briefing").status_code == 200 and len(gw.calls) == 3
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "select trigger_key, on_behalf_of_user_id, result from ai_runs "
                "where business_id=:b and use_case='briefing'"
            ),
            {"b": biz},
        ).all()
    assert len(rows) == 3 and len({r.on_behalf_of_user_id for r in rows}) == 2
    assert all(r.trigger_key.startswith(f"briefing:{r.on_behalf_of_user_id}:") for r in rows)
    assert rows[0].result in [first.model_dump(), second.model_dump()]


def test_briefing_respects_user_permissions(registry: Registry, owner_engine: Engine) -> None:
    demo = seed_demo(owner_engine)
    set_registry(registry)  # seed_demo builds its own registry; restore this test's API/session registry.
    gw = FakeGateway([Briefing(bullets=["Review stock."]).model_dump_json()] * 2)
    set_router_for_tests(fake_router_by_class(writer=gw))
    app = create_app(registry)
    for perms in (
        {"ai.assistant.use", "org.business.read"},
        {"ai.assistant.use", "org.business.read", "inventory.stock.read"},
    ):
        response = client_for(app, registry, demo.business, perms).get("/api/v1/ai/briefing")
        assert response.status_code == 200, response.text
    hidden, stock = [c.messages[1].content for c in gw.calls]
    assert '"agenda": null' in hidden and '"low_stock": null' in hidden
    assert "Cable set" not in hidden and "Cable set" in stock
    assert "Ada Example" not in stock and "Brake service" not in stock
    assert client_for(app, registry, demo.business, set()).get("/api/v1/ai/briefing").status_code == 403


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_briefing_rejects_invented_numbers(registry: Registry, owner_engine: Engine, repair_succeeds: bool) -> None:
    biz = make_business()
    bad = Briefing(bullets=["There are 987654321 appointments."]).model_dump_json()
    good = Briefing(bullets=["Review today's work."]).model_dump_json()
    gw = FakeGateway([bad, good if repair_succeeds else bad])
    set_router_for_tests(fake_router_by_class(writer=gw))
    client = client_for(create_app(registry), registry, biz, {"ai.assistant.use", "org.business.read"})
    response = client.get("/api/v1/ai/briefing")
    assert len(gw.calls) == 2
    assert response.status_code == (200 if repair_succeeds else 502), response.text
    if not repair_succeeds:
        assert response.json()["code"] == "ai.invalid_model_output"
    with owner_engine.connect() as conn:
        row = conn.execute(text("select status, result from ai_runs where business_id=:b"), {"b": biz}).one()
    assert row.status == ("succeeded" if repair_succeeds else "failed")
    assert "987654321" not in str(row.result)


def test_briefing_with_only_ai_permission(registry: Registry) -> None:
    biz = make_business()
    gw = FakeGateway([Briefing(bullets=["Ask staff about today's work."]).model_dump_json()])
    set_router_for_tests(fake_router_by_class(writer=gw))
    response = client_for(create_app(registry), registry, biz, {"ai.assistant.use"}).get("/api/v1/ai/briefing")
    assert response.status_code == 200, response.text
    facts = gw.calls[0].messages[1].content
    for field in ("agenda", "waiting_conversations", "approvals", "low_stock", "delivery_issues"):
        assert f'"{field}": null' in facts


@pytest.mark.parametrize(
    "output,facts",
    [
        ("5 items", "50 items"),
        ("2.5 units", "2 and 5 units"),
        ("-3 units", "3 units"),
        ("three items", "two items"),
        (".5 units", "5 units"),
    ],
)
def test_number_validation_uses_complete_tokens(output: str, facts: str) -> None:
    with pytest.raises(ValueError, match="Every number"):
        validate_numbers(output, facts)


def test_number_validation_accepts_given_decimals_and_counts() -> None:
    validate_numbers("5 items, 2.5 units, -3, 1,000 and two", "5, 2.5, -3, 1,000, two")
