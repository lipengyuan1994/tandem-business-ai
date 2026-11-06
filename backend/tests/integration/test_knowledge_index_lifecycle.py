from typing import Any

import pytest
from sqlalchemy import select

from app.modules.ai.embeddings import EmbeddingGateway
from app.modules.ai.gateway.router import load_router
from app.modules.knowledge import indexing, repository, service
from app.modules.knowledge.chunking import chunks
from app.modules.knowledge.models import Chunk
from app.modules.knowledge.schemas import ArticleEdit
from app.platform import jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.models import Job
from tests.integration.test_knowledge_hybrid import publish

pytest_plugins = ["tests.integration.test_knowledge_hybrid"]


def test_index_inference_uses_ai_worker_lane(ctx: Context, registry: Any) -> None:
    publish(ctx)
    assert jobs.claim("c2a-non-ai", lane="non_ai") is None
    job = jobs.claim("c2a-ai", lane="ai")
    assert job is not None and job["kind"] == "ai.knowledge_index"
    assert jobs.run_job(registry, job)


def test_durable_publish_job_and_model_change(ctx: Context, registry: Any) -> None:
    ident = publish(ctx)
    with transaction(ctx) as db:
        row = db.scalar(select(Job).where(Job.kind == "ai.knowledge_index"))
        assert row is not None and row.payload == {"article_id": str(ident)}
    assert jobs.run_pending(registry, limit=1) == 1
    with transaction(ctx) as db:
        assert repository.get(db, ctx, ident).search_index_state == "indexed"
        assert db.scalar(select(Chunk)).model_id == "fake/fake-embed"
    router = load_router()
    router.routes.routes["embed"][0].model = "fake-embed-v2"
    indexing.sweep(ctx, {})
    with transaction(ctx) as db:
        assert list(db.scalars(select(Chunk))) == []
        assert repository.get(db, ctx, ident).search_index_state == "pending"
    assert jobs.run_pending(registry, limit=1) == 1
    with transaction(ctx) as db:
        assert db.scalar(select(Chunk)).model_id == "fake/fake-embed-v2"
        assert service.search_public(db, ctx, "dog")[0].article_id == ident


def test_publication_change_during_inference_discards_result(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = publish(ctx)
    original = EmbeddingGateway.embed

    def embed(inner_ctx: Context, texts: list[str], **kw: Any) -> Any:
        result = original(inner_ctx, texts, **kw)
        with transaction(ctx) as db:
            a = repository.get(db, ctx, ident)
            service.archive(db, ctx, ident, a.version)
        return result

    monkeypatch.setattr(EmbeddingGateway, "embed", staticmethod(embed))
    indexing.index_article(ctx, {"article_id": str(ident)})
    with transaction(ctx) as db:
        assert list(db.scalars(select(Chunk))) == []


def test_narrowing_visibility_drops_chunks_immediately(ctx: Context) -> None:
    ident = publish(ctx)
    indexing.index_article(ctx, {"article_id": str(ident)})
    with transaction(ctx) as db:
        a = repository.get(db, ctx, ident)
        service.edit(
            db,
            ctx,
            ident,
            ArticleEdit(version=a.version, title="Pets", body_markdown="Staff only", visibility="internal"),
        )
        assert list(db.scalars(select(Chunk))) == []
        assert service.search_public(db, ctx, "pets") == []


def test_sweep_is_bounded_and_reindex_permission(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.platform.context import system_context
    from app.platform.errors import Forbidden

    publish(ctx)
    publish(ctx, "second")
    monkeypatch.setattr(indexing, "BATCH", 1)
    indexing.sweep(ctx, {"force": True})
    with transaction(ctx) as db:
        assert len(list(db.scalars(select(Job).where(Job.kind == "knowledge.reindex")))) == 1
        with pytest.raises(Forbidden):
            indexing.reindex(db, system_context(ctx.business_id))


def test_chunk_spans_overlap_keep_headings_and_are_bounded() -> None:
    body = "# Pets\n\n" + "friendly " * 450 + "\n# Access\n\n" + "accessible " * 450
    values = chunks(body)
    assert len(values) == 3
    assert all(v.text == body[v.start : v.end] and v.tokens > 0 for v in values)
    assert all(len(v.text.split()) <= 400 for v in values)
    assert values[1].start < values[0].end and values[2].end == len(body.rstrip())


def test_unpublish_and_deleted_version_remove_chunks(ctx: Context, owner_engine: Any) -> None:
    from sqlalchemy import text

    ident = publish(ctx)
    indexing.index_article(ctx, {"article_id": str(ident)})
    with transaction(ctx) as db:
        a = repository.get(db, ctx, ident)
        service.unpublish(db, ctx, ident, a.version)
        assert list(db.scalars(select(Chunk))) == []
        assert service.search_public(db, ctx, "pets") == []
        service.publish(db, ctx, ident, a.version)
        version = a.published_version
    indexing.index_article(ctx, {"article_id": str(ident)})
    with owner_engine.begin() as db:
        db.execute(
            text("delete from kb_article_versions where article_id=:a and version_no=:v"), {"a": ident, "v": version}
        )
    with transaction(ctx) as db:
        assert list(db.scalars(select(Chunk))) == []


@pytest.mark.parametrize("failure", [False, True])
def test_query_visibility_is_read_after_embedding(ctx: Context, monkeypatch: pytest.MonkeyPatch, failure: bool) -> None:
    ident = publish(ctx)
    indexing.index_article(ctx, {"article_id": str(ident)})
    original = EmbeddingGateway.embed

    def embed(inner_ctx: Context, texts: list[str], **kw: Any) -> Any:
        result = original(inner_ctx, texts, **kw)
        with transaction(ctx) as db:
            article = repository.get(db, ctx, ident)
            service.archive(db, ctx, ident, article.version)
        if failure:
            raise RuntimeError("embedding unavailable")
        return result

    monkeypatch.setattr(EmbeddingGateway, "embed", staticmethod(embed))
    with transaction(ctx) as db:
        assert service.search_public(db, ctx, "pets") == []
