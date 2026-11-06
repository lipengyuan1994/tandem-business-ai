"""AI-01: ai tables, status endpoint and data lifecycle (docs/roadmap/ai/cards/AI-01-module-skeleton.md)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.main import create_app
from app.modules.ai.errors import AiError
from app.modules.ai.models import AiEffect, AiRun, AiRunStep, AiUsageDaily, AssistantMessage, AssistantThread
from app.modules.ai.public import status
from app.modules.ai.settings import api_key
from app.modules.ai.tests.helpers import AI_TABLES, fill_all_tables, insert_run, system_ctx
from app.modules.org.testing import make_business
from app.platform.config import get_settings
from app.platform.db import transaction
from app.platform.errors import DomainError
from app.platform.registry import ErasureTarget, Registry
from app.platform.testing.auth import client_for

MODELS = (AiRun, AiRunStep, AiEffect, AiUsageDaily, AssistantThread, AssistantMessage)


@pytest.fixture
def routes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Since AI-03 `status` also needs an `agent` route whose provider has a key (test value, never sent anywhere).
    path = tmp_path / "ai-routes.yaml"
    path.write_text(
        "providers:\n  vultr: {kind: openai_compat, base_url: 'https://api.vultrinference.com/v1',"
        " api_key_env: VULTR_INFERENCE_API_KEY, vendor: vultr}\n"
        "routes:\n  agent: [{provider: vultr, model: example-model}]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(get_settings(), "ai_routes_file", str(path))
    monkeypatch.setattr(get_settings(), "vultr_inference_api_key", SecretStr("test-key-not-real"))
    return path


@pytest.fixture
def no_routes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "missing" / "ai-routes.yaml"
    monkeypatch.setattr(get_settings(), "ai_routes_file", str(path))
    return path


# ------------------------------------------------------------------------------------------------- tables


def test_ai_tables_have_forced_rls_and_business_scope(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                """select c.relname, c.relrowsecurity, c.relforcerowsecurity,
                          exists (select 1 from pg_policies p where p.tablename = c.relname
                                   and p.policyname = 'business_isolation') as has_policy
                     from pg_class c join pg_namespace n on n.oid = c.relnamespace
                    where n.nspname = 'public' and c.relname = any(:names)"""
            ),
            {"names": list(AI_TABLES)},
        ).all()
    assert sorted(r.relname for r in rows) == sorted(AI_TABLES)
    assert all(r.relrowsecurity and r.relforcerowsecurity and r.has_policy for r in rows), rows

    a, b = make_business("example-cycle-works"), make_business("example-garden-store")
    fill_all_tables(system_ctx(a))
    with transaction(system_ctx(b)) as db:
        for model in MODELS:
            assert db.scalar(select(func.count()).select_from(model)) == 0, model.__tablename__
    with transaction(system_ctx(a)) as db:
        for model in MODELS:
            assert db.scalar(select(func.count()).select_from(model)) == 1, model.__tablename__

    # Writing a row for another business is refused by the policy's WITH CHECK.
    with pytest.raises(ProgrammingError), transaction(system_ctx(b)) as db:
        db.add(
            AiRun(
                business_id=a, use_case="copilot", trigger_type="api", trigger_key="copilot:x", prompt_name="copilot",
                prompt_version=1, model_class="agent", status="running",
            )
        )  # fmt: skip
        db.flush()


@pytest.mark.parametrize("model", [AiRunStep, AiEffect])
def test_ai_run_steps_append_only(model: type[AiRunStep] | type[AiEffect]) -> None:
    ctx = system_ctx(make_business())
    fill_all_tables(ctx)
    with pytest.raises(DBAPIError, match="permission denied"), transaction(ctx) as db:
        db.execute(update(model).values(version=model.version + 1))
    with pytest.raises(DBAPIError, match="permission denied"), transaction(ctx) as db:
        db.execute(model.__table__.delete())
    with transaction(ctx) as db:
        assert db.scalar(select(func.count()).select_from(model)) == 1


def test_run_step_numbers_are_unique_per_run() -> None:
    ctx = system_ctx(make_business())
    run_id = insert_run(ctx)
    with transaction(ctx) as db:
        db.add(AiRunStep(business_id=ctx.business_id, run_id=run_id, step_no=1, kind="final"))
    with pytest.raises(DBAPIError), transaction(ctx) as db:
        db.add(AiRunStep(business_id=ctx.business_id, run_id=run_id, step_no=1, kind="final"))
        db.flush()


# ------------------------------------------------------------------------------------------------- status


def test_status_not_configured_without_routes_file(no_routes_file: Path) -> None:
    ctx = system_ctx(make_business())
    with transaction(ctx) as db:
        result = status(db, ctx)
    assert result.enabled is False
    assert result.reason == "ai.not_configured"
    assert result.policy == "hosted_vultr"
    assert result.budget_percent == 0


def test_status_not_configured_when_routes_path_is_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_routes_file", str(tmp_path))  # a directory cannot be read as a file
    ctx = system_ctx(make_business())
    with transaction(ctx) as db:
        assert status(db, ctx).reason == "ai.not_configured"


def test_status_enabled_with_routes_file(routes_file: Path) -> None:
    ctx = system_ctx(make_business())
    with transaction(ctx) as db:
        result = status(db, ctx)
    assert (result.enabled, result.reason, result.policy, result.budget_percent) == (True, None, "hosted_vultr", 0)


def test_status_policy_regulated_when_data_profile_regulated(
    routes_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = system_ctx(make_business())
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    with transaction(ctx) as db:
        assert status(db, ctx).policy == "regulated"
    monkeypatch.setattr(get_settings(), "data_profile", "standard")
    with transaction(ctx) as db:
        assert status(db, ctx).policy == "hosted_vultr"


def test_status_requires_assistant_permission(routes_file: Path, registry: Registry) -> None:
    business = make_business()
    app = create_app(registry)
    denied = client_for(app, registry, business, {"ai.settings.read", "org.business.read"})
    r = denied.get("/api/v1/ai/status")
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "auth.permission_denied"

    allowed = client_for(app, registry, business, {"ai.assistant.use"})
    r = allowed.get("/api/v1/ai/status")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "enabled": True, "reason": None, "policy": "hosted_vultr", "budget_percent": 0,
        "model_classes": {
            "agent": {"enabled": True, "reason": None},
            **{c: {"enabled": False, "reason": f"ai.not_configured: no usable route for {c}"}
               for c in ("fast_extract", "writer", "safety", "grounding", "embed", "stt", "tts")},
        },
    }  # fmt: skip


def test_status_endpoint_reports_not_configured(no_routes_file: Path, registry: Registry) -> None:
    business = make_business()
    client = client_for(create_app(registry), registry, business, {"ai.assistant.use"})
    r = client.get("/api/v1/ai/status")
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    assert r.json()["reason"] == "ai.not_configured"


# ------------------------------------------------------------------------------------------------- lifecycle


def test_export_includes_ai_tables(registry: Registry) -> None:
    a, b = make_business("example-cycle-works"), make_business("example-garden-store")
    ids = fill_all_tables(system_ctx(a))
    fill_all_tables(system_ctx(b))
    with transaction(system_ctx(a)) as db:
        exported = registry.export_handlers["ai"](db, system_ctx(a))
    assert set(exported) == set(AI_TABLES)
    assert all(len(rows) == 1 for rows in exported.values()), {k: len(v) for k, v in exported.items()}
    assert exported["ai_runs"][0]["id"] == str(ids["run"])
    assert exported["assistant_messages"][0]["content"] == "What is low on stock?"
    assert all(row["business_id"] == str(a) for rows in exported.values() for row in rows)


def test_erasure_scrubs_ai_texts(registry: Registry) -> None:
    ctx = system_ctx(make_business())
    message_id, proposal_id = uuid.uuid4(), uuid.uuid4()
    named = insert_run(ctx, result={"text": "Replied to Ada Example about brakes", "count": 2})
    linked = insert_run(ctx, trigger_key=f"intake:{message_id}", result={"text": "Booked a brake service"})
    by_effect = insert_run(ctx, result={"text": "Proposed a reply"})
    unrelated = insert_run(ctx, result={"text": "Reordered brake pads"})
    with transaction(ctx) as db:
        db.add(
            AiEffect(
                business_id=ctx.business_id, trigger_key="copilot:1", effect_key="b" * 64, kind="proposal",
                ref_id=proposal_id, run_id=by_effect,
            )
        )  # fmt: skip
        thread = AssistantThread(business_id=ctx.business_id, user_id=uuid.uuid4(), title="About Ada Example")
        db.add(thread)
        db.flush()
        messages = {
            "named": AssistantMessage(
                business_id=ctx.business_id, thread_id=thread.id, role="user",
                content="Draft a reply to ada@example.com please",
            ),
            "proposal": AssistantMessage(
                business_id=ctx.business_id, thread_id=thread.id, role="assistant",
                content="I proposed a booking for the customer.", proposal_ids=[proposal_id],
            ),
            "unrelated": AssistantMessage(
                business_id=ctx.business_id, thread_id=thread.id, role="user", content="What is low on stock?"
            ),
        }  # fmt: skip
        db.add_all(messages.values())
        db.flush()
        message_ids = {k: m.id for k, m in messages.items()}

    target = ErasureTarget(
        uuid.uuid4(), "Ada Example", ("ada@example.com",), (), linked_ids=frozenset({message_id, proposal_id})
    )
    with transaction(ctx) as db:
        counts = registry.erasure_handlers["ai"](db, ctx, target)
    assert counts["ai_runs_redacted"] == 3
    assert counts["assistant_messages_redacted"] == 2

    with transaction(ctx) as db:
        results = {run_id: db.get(AiRun, run_id).result for run_id in (named, linked, by_effect, unrelated)}  # type: ignore[union-attr]
        contents = {k: db.get(AssistantMessage, i).content for k, i in message_ids.items()}  # type: ignore[union-attr]
        title = db.get(AssistantThread, thread.id).title  # type: ignore[union-attr]
    assert "Ada Example" not in results[named]["text"]
    assert results[named]["count"] == 2
    assert results[linked] == {"text": "[erased]"}
    assert results[by_effect] == {"text": "[erased]"}
    assert results[unrelated] == {"text": "Reordered brake pads"}
    assert "ada@example.com" not in contents["named"]
    assert contents["proposal"] == "[erased]"
    assert contents["unrelated"] == "What is low on stock?"
    assert "Ada Example" not in title


# ------------------------------------------------------------------------------------------------- errors, settings


def test_ai_error_codes_map_to_http_statuses() -> None:
    assert [AiError(c).status for c in ("ai.not_configured", "ai.disabled_by_policy")] == [409, 409]
    assert AiError("ai.budget_exceeded").status == 429
    assert AiError("ai.provider_unavailable").status == 503
    assert {AiError(c).status for c in ("ai.invalid_model_output", "ai.step_limit", "ai.run_timeout")} == {502}
    assert isinstance(AiError("ai.step_limit"), DomainError)
    with pytest.raises(ValueError):
        AiError("ai.something_else")


def test_api_key_reads_only_provider_key_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "vultr_inference_api_key", SecretStr("test-key-not-real"))
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr(""))
    assert api_key("VULTR_INFERENCE_API_KEY") == "test-key-not-real"
    assert api_key("OPENAI_API_KEY") == ""
    assert api_key("APP_SECRET_KEY") == ""  # a route file can never point a provider at another secret
    assert api_key(None) == ""
