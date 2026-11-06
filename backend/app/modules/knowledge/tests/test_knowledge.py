from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError

from app.main import create_app
from app.modules.conversations import public as conversations
from app.modules.identity.testing import login_as
from app.modules.knowledge import public as knowledge
from app.modules.knowledge import service
from app.modules.knowledge.models import Article, ArticleVersion, Suggestion
from app.modules.knowledge.schemas import ArticleCreate, ArticleEdit
from app.modules.org.testing import make_business
from app.platform.context import Context, system_context
from app.platform.db import transaction
from app.platform.errors import Conflict, Forbidden


@pytest.fixture
def ctx(registry: Any) -> Context:
    return system_context(make_business(), frozenset({"org.settings.write", "conversations.thread.read"}))


def draft(db: Any, ctx: Context, slug: str = "warranty", **kw: Any) -> Article:
    return service.create(
        db,
        ctx,
        ArticleCreate(
            title="Repair warranty",
            slug=slug,
            body_markdown="Our repair warranty covers workmanship for thirty days.",
            **kw,
        ),
    )


def test_versions_publication_search_archive(ctx: Context) -> None:
    with transaction(ctx) as db:
        a = draft(db, ctx)
        assert knowledge.search_public(db, ctx, "warranty") == []
        service.publish(db, ctx, a.id, a.version)
        hit = knowledge.search_public(db, ctx, "warranty")[0]
        assert hit.article_id == a.id and hit.version == 2
        service.edit(
            db,
            ctx,
            a.id,
            ArticleEdit(
                version=a.version, title="Revised warranty", body_markdown="A secret draft about warranty.", tags=[]
            ),
        )
        assert knowledge.search_public(db, ctx, "warranty")[0].version == hit.version
        assert "secret" not in str(knowledge.search_public(db, ctx, "warranty"))
        service.publish(db, ctx, a.id, a.version)
        assert knowledge.search_public(db, ctx, "warranty")[0].version == 4
        versions = service.get_versions(db, ctx, a.id)
        assert [v.version_no for v in versions] == [1, 2, 3, 4]
        service.archive(db, ctx, a.id, a.version)
        assert knowledge.search_public(db, ctx, "warranty") == []
        assert (
            db.scalar(
                text("select count(*) from audit_events where action in ('knowledge.published','knowledge.archived')")
            )
            == 3
        )


def test_public_visibility_ranking_bounds_and_permissions(ctx: Context) -> None:
    with transaction(ctx) as db:
        a = draft(db, ctx)
        service.publish(db, ctx, a.id, a.version)
        internal = draft(db, ctx, "internal", visibility="internal")
        service.publish(db, ctx, internal.id, internal.version)
        broad = service.create(
            db, ctx, ArticleCreate(title="Other topics", slug="other", body_markdown="warranty " + "garden " * 2000)
        )
        service.publish(db, ctx, broad.id, broad.version)
        hits = knowledge.search_public(db, ctx, "warranty")
        assert hits[0].article_id == a.id and internal.id not in {h.article_id for h in hits}
        assert all(sum(map(len, h.passages)) <= 700 for h in hits)
        assert internal.id in {h.article_id for h in knowledge.search_staff(db, ctx, "warranty")}
        assert knowledge.search_public(db, ctx, "") == []
        with pytest.raises(Conflict):
            service.archive(db, ctx, a.id, 999)
        denied = system_context(ctx.business_id)
        with pytest.raises(Forbidden):
            knowledge.search_staff(db, denied, "warranty")
        with pytest.raises(Forbidden):
            draft(db, system_context(ctx.business_id, frozenset({"conversations.thread.read"})), "denied")


def test_rls_and_append_only_versions(ctx: Context, owner_engine: Engine) -> None:
    with transaction(ctx) as db:
        a = draft(db, ctx)
        article_id = a.id
        conv = conversations.start_conversation(db, ctx, channel="web_chat", author_kind="visitor", body="Insurance?")
        knowledge.suggest(db, ctx, conv, "Insurance?")
        assert len(service.list_suggestions(db, ctx)) == 1
    other = system_context(make_business("example-garden-store"))
    with transaction(other) as db:
        assert list(db.scalars(select(Article))) == []
        assert list(db.scalars(select(ArticleVersion))) == []
        assert list(db.scalars(select(Suggestion))) == []
    for sql in ("update kb_article_versions set title=title", "delete from kb_article_versions"):
        with pytest.raises(DBAPIError), transaction(ctx) as db:
            db.execute(text(sql))
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from kb_article_versions where article_id=:a"), {"a": article_id}) == 1
    with pytest.raises(DBAPIError), transaction(other) as db:
        db.add(Article(business_id=ctx.business_id, title="Foreign write", slug="foreign"))
        db.flush()


def test_suggestions_redact_dedupe_dismiss_convert(ctx: Context) -> None:
    with transaction(ctx) as db:
        conv = conversations.start_conversation(db, ctx, channel="web_chat", author_kind="visitor", body="Policy?")
        question = "Do you insure bikes? Contact ada@example.com or +12025550123."
        knowledge.suggest(db, ctx, conv, question)
        knowledge.suggest(db, ctx, conv, question)
        rows = service.list_suggestions(db, ctx)
        assert (
            len(rows) == 1
            and "ada@example.com" not in rows[0].question_text
            and "12025550123" not in rows[0].question_text
        )
        s = rows[0]
        a = service.convert(
            db, ctx, s.id, s.version, ArticleCreate(title="Insurance", slug="insurance", body_markdown="Ask us.")
        )
        assert s.status == "done" and s.article_id == a.id and a.status == "draft"
        knowledge.suggest(db, ctx, conv, "Another question?")
        s = service.list_suggestions(db, ctx)[0]
        service.dismiss(db, ctx, s.id, s.version)
        assert s.status == "dismissed"


def test_staff_api_permissions_versions_validation(ctx: Context) -> None:
    app = create_app()
    owner = login_as(app, ctx.business_id, ["owner"])
    operator = login_as(app, ctx.business_id, ["operator"])
    url = "/api/v1/knowledge/articles"
    body = {"title": "Returns", "slug": "returns", "body_markdown": "Bring your receipt."}
    assert operator.post(url, json=body).status_code == 403
    made = owner.post(url, json=body)
    assert made.status_code == 201, made.text
    a = made.json()
    assert operator.get(url).status_code == 200
    assert owner.post(f"{url}/{a['id']}/publish", json={"version": a["version"]}).status_code == 200
    detail = owner.get(f"{url}/{a['id']}").json()
    assert len(detail["versions"]) == 2
    assert owner.post(url, json=body).status_code == 409
    assert owner.post(url, json={**body, "body_markdown": "x" * 20001}).status_code == 422
    assert TestClient(app).get(url).status_code == 401


def test_visibility_widening_requires_republish_and_erasure_handles_duplicate_questions(ctx: Context) -> None:
    from app.modules.knowledge import lifecycle
    from app.platform.registry import ErasureTarget

    with transaction(ctx) as db:
        a = draft(db, ctx, visibility="internal")
        service.publish(db, ctx, a.id, a.version)
        service.edit(
            db,
            ctx,
            a.id,
            ArticleEdit(
                version=a.version,
                title="Repair warranty",
                body_markdown="New public warranty text.",
                visibility="public",
            ),
        )
        assert knowledge.search_public(db, ctx, "warranty") == []
        service.publish(db, ctx, a.id, a.version)
        assert knowledge.search_public(db, ctx, "warranty")
        conv = conversations.start_conversation(db, ctx, channel="web_chat", author_kind="visitor", body="Insurance?")
        knowledge.suggest(db, ctx, conv, "First question?")
        knowledge.suggest(db, ctx, conv, "Second question?")
        target = ErasureTarget(
            customer_id=uuid.uuid4(), display_name="Ada Example", emails=(), phones=(), linked_ids=frozenset({conv})
        )
        assert lifecycle.erase(db, ctx, target)["kb_suggestions_redacted"] == 2
        assert service.list_suggestions(db, ctx) == []
