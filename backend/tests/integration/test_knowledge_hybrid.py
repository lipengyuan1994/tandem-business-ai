from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.modules.ai.gateway.fake import FakeGateway, fake_router
from app.modules.ai.gateway.router import set_router_for_tests
from app.modules.knowledge import indexing, lifecycle, repository, service
from app.modules.knowledge.models import Article, Chunk
from app.modules.knowledge.schemas import ArticleCreate, ArticleEdit
from app.modules.org.testing import make_business
from app.platform.context import Context, system_context
from app.platform.db import transaction
from app.platform.registry import ErasureTarget


@pytest.fixture
def ctx(registry: Any) -> Any:
    set_router_for_tests(fake_router(FakeGateway([])))
    yield system_context(make_business(), frozenset({"org.settings.write", "conversations.thread.read"}))
    set_router_for_tests(None)


def publish(
    ctx: Context, slug: str = "pets", *, visibility: str = "public", body: str = "Pets policy: pets welcome."
) -> Any:
    with transaction(ctx) as db:
        a = service.create(
            db, ctx, ArticleCreate(title=slug.title(), slug=slug, visibility=visibility, body_markdown=body)
        )
        service.publish(db, ctx, a.id, a.version)
        return a.id


def test_publish_index_paraphrase_exact_and_unavailable(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = publish(ctx)
    indexing.index_article(ctx, {"article_id": str(ident)})
    with transaction(ctx) as db:
        assert repository.search(db, ctx, "dog", 5) == []
        hits = service.search_public(db, ctx, "dog")
        assert hits[0].article_id == ident and hits[0].version == 2
        chunk = db.scalar(select(Chunk))
        assert chunk is not None and len(chunk.embedding) == 1024
        assert hits[0].spans[0].start == chunk.start_offset
        assert service.search_public(db, ctx, "pets")[0].article_id == ident
    from app.modules.ai import embeddings

    monkeypatch.setattr(embeddings.EmbeddingGateway, "embed", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    with transaction(ctx) as db:
        assert service.search_public(db, ctx, "pets")[0].article_id == ident
        assert service.search_public(db, ctx, "dog") == []


def test_versions_archive_private_and_rls(ctx: Context) -> None:
    ident = publish(ctx)
    private = publish(ctx, "internal", visibility="internal")
    indexing.index_article(ctx, {"article_id": str(ident)})
    indexing.index_article(ctx, {"article_id": str(private)})
    with transaction(ctx) as db:
        a = repository.get(db, ctx, ident)
        service.edit(
            db, ctx, ident, ArticleEdit(version=a.version, title="Pets", body_markdown="Pets require a leash.")
        )
        assert db.scalar(select(Chunk)).version_no == 2
        service.publish(db, ctx, ident, a.version)
        assert list(db.scalars(select(Chunk))) == []
    indexing.index_article(ctx, {"article_id": str(ident)})
    indexing.index_article(ctx, {"article_id": str(ident)})
    with transaction(ctx) as db:
        chunks = list(db.scalars(select(Chunk)))
        assert len(chunks) == 1 and chunks[0].version_no == 4
        assert private not in {c.article_id for c in chunks}
    other = replace(ctx, business_id=make_business("example-garden-store"))
    with transaction(other) as db:
        assert list(db.scalars(select(Chunk))) == []
        assert service.search_public(db, other, "dog") == []
    with pytest.raises(DBAPIError), transaction(other) as db:
        db.execute(text("insert into kb_chunks select * from kb_chunks where false"))
        db.add(
            Chunk(
                business_id=ctx.business_id,
                article_id=ident,
                version_no=4,
                chunk_index=1,
                start_offset=0,
                end_offset=1,
                text="x",
                token_estimate=1,
                embedding=[1.0] * 1024,
                model_id="x",
            )
        )
        db.flush()
    with transaction(ctx) as db:
        a = repository.get(db, ctx, ident)
        service.archive(db, ctx, ident, a.version)
        assert list(db.scalars(select(Chunk))) == []
        assert service.search_public(db, ctx, "dog") == []


def test_export_erasure_and_index_failure_visible(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = publish(ctx)
    indexing.index_article(ctx, {"article_id": str(ident)})
    with transaction(ctx) as db:
        exported = lifecycle.export(db, ctx)
        chunk = next(iter(exported["kb_chunks"]))
        assert "embedding" not in chunk and "text" not in chunk and chunk["model_id"] == "fake/fake-embed"
        lifecycle.erase(db, ctx, ErasureTarget(customer_id=ident, display_name="Example", emails=(), phones=()))
        assert list(db.scalars(select(Chunk))) == []
    from app.modules.ai.embeddings import EmbeddingGateway

    monkeypatch.setattr(EmbeddingGateway, "embed", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("untrusted")))
    with pytest.raises(RuntimeError):
        indexing.index_article(ctx, {"article_id": str(ident)})
    with transaction(ctx) as db:
        a = db.get(Article, ident)
        assert a.search_index_state == "failed" and a.search_index_error == "knowledge.index_failed"
