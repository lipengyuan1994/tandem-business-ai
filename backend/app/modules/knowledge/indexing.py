"""Durable article-sized indexing and bounded model-change sweeps. No transaction spans inference."""

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.platform import jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.registry import get_registry

from .chunking import chunks
from .models import Article, ArticleVersion, Chunk

BATCH = 25


def invalidate(db: Session, ctx: Context, article: Article, *, pending: bool = False) -> None:
    db.execute(delete(Chunk).where(Chunk.business_id == ctx.business_id, Chunk.article_id == article.id))
    article.index_generation += 1
    article.search_index_state = "pending" if pending else "not_applicable"
    article.search_index_error = None
    article.search_index_model = None
    db.flush()


def schedule(db: Session, ctx: Context, article: Article) -> None:
    public = article.status == "published" and article.visibility == "public"
    invalidate(db, ctx, article, pending=public)
    if public:
        jobs.enqueue(
            db,
            "ai.knowledge_index",
            {"article_id": str(article.id)},
            business_id=ctx.business_id,
            dedupe_key=f"kb-index:{ctx.business_id}:{article.id}:{article.index_generation}",
            ctx=ctx,
        )


def _get(db: Session, ctx: Context, ident: uuid.UUID, *, lock: bool = False) -> Article | None:
    stmt = select(Article).where(Article.business_id == ctx.business_id, Article.id == ident)
    return db.scalar(stmt.with_for_update() if lock else stmt)


def _public(article: Article) -> bool:
    return article.status == "published" and article.visibility == "public"


def index_article(ctx: Context, payload: dict[str, Any]) -> None:
    ident = uuid.UUID(payload["article_id"])
    provider = get_registry().knowledge_embeddings
    model = provider.model(ctx) if provider else None
    with transaction(ctx) as db:
        a = _get(db, ctx, ident, lock=True)
        if a is None:
            return
        if not _public(a):
            invalidate(db, ctx, a)
            return
        if model is None:
            invalidate(db, ctx, a)
            a.search_index_state = "unavailable"
            a.search_index_error = "knowledge.embedding_unavailable"
            return
        if a.search_index_state == "indexed" and a.search_index_model == model:
            return
        version, generation = a.published_version, a.index_generation
        v = db.scalar(
            select(ArticleVersion).where(
                ArticleVersion.business_id == ctx.business_id,
                ArticleVersion.article_id == ident,
                ArticleVersion.version_no == version,
            )
        )
        assert v is not None
        spans = chunks(v.body_markdown)
        title = v.title
    assert provider is not None
    try:
        if not spans or len(spans) > 32:
            raise ValueError("knowledge.chunk_limit")
        embedded = provider.embed(ctx, [title + "\n" + s.text for s in spans])
        if len(embedded.vectors) != len(spans):
            raise ValueError("knowledge.embedding_count")
        # A route edit or profile change during inference invalidates the result, even at the same dimensions.
        if provider.model(ctx) != model:
            return
        with transaction(ctx) as db:
            a = _get(db, ctx, ident, lock=True)
            if a is None or not _public(a) or (a.published_version, a.index_generation) != (version, generation):
                return
            db.execute(delete(Chunk).where(Chunk.business_id == ctx.business_id, Chunk.article_id == ident))
            for i, (span, vector) in enumerate(zip(spans, embedded.vectors, strict=True)):
                db.add(
                    Chunk(
                        business_id=ctx.business_id,
                        article_id=ident,
                        version_no=version,
                        chunk_index=i,
                        start_offset=span.start,
                        end_offset=span.end,
                        text=span.text,
                        token_estimate=span.tokens,
                        embedding=vector,
                        model_id=embedded.model_id,
                    )
                )
            a.search_index_state, a.search_index_model, a.search_index_error = "indexed", embedded.model_id, None
    except Exception:
        with transaction(ctx) as db:
            a = _get(db, ctx, ident, lock=True)
            if a is not None and _public(a) and (a.published_version, a.index_generation) == (version, generation):
                a.search_index_state, a.search_index_error = "failed", "knowledge.index_failed"
        # Job traceback must not include raw provider, PDF or database parameter text.
        raise RuntimeError("knowledge.index_failed") from None


def reindex(db: Session, ctx: Context) -> uuid.UUID:
    ctx.require("org.settings.write")
    return jobs.enqueue(db, "knowledge.reindex", {"force": True}, business_id=ctx.business_id, ctx=ctx)


def sweep(ctx: Context, payload: dict[str, Any]) -> None:
    provider = get_registry().knowledge_embeddings
    model = provider.model(ctx) if provider else None
    with transaction(ctx) as db:
        stmt = select(Article).where(
            Article.business_id == ctx.business_id, Article.status == "published", Article.visibility == "public"
        )
        if payload.get("after"):
            stmt = stmt.where(Article.id > uuid.UUID(payload["after"]))
        articles = list(db.scalars(stmt.order_by(Article.id).limit(BATCH).with_for_update()))
        for a in articles:
            if model is None:
                invalidate(db, ctx, a)
                a.search_index_state, a.search_index_error = "unavailable", "knowledge.embedding_unavailable"
            elif payload.get("force") or a.search_index_model != model or a.search_index_state != "indexed":
                schedule(db, ctx, a)
        if len(articles) == BATCH:
            jobs.enqueue(
                db,
                "knowledge.reindex",
                {**payload, "after": str(articles[-1].id)},
                business_id=ctx.business_id,
                ctx=ctx,
            )
