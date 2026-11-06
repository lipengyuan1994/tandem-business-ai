"""Tenant/publication filtered candidate retrieval, then reciprocal rank fusion (k=60)."""

import logging

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.registry import get_registry

from . import repository
from .models import Chunk
from .schemas import SearchHit, SearchSpan
from .vector import Vector

log = logging.getLogger(__name__)


def search(db: Session, ctx: Context, query: str, limit: int) -> list[SearchHit]:
    if not query.strip():
        return []
    provider = get_registry().knowledge_embeddings
    if provider is None:
        return repository.search(db, ctx, query, limit)
    try:
        model = provider.model(ctx)
        if (
            model is None
            or db.scalar(select(Chunk.id).where(Chunk.business_id == ctx.business_id, Chunk.model_id == model).limit(1))
            is None
        ):
            return repository.search(db, ctx, query, limit)
        embedded = provider.embed(ctx, [query[:500]], query=True)
    except Exception:
        # A visitor still receives full-text results. Never log query/provider exception content.
        log.info("knowledge_query_embedding_unavailable")
        return repository.search(db, ctx, query, limit)
    # Read publication/visibility after the potentially slow call, including the FTS fallback above.
    lexical = repository.search(db, ctx, query, 10)
    # Materialization forces tenant/visibility/publication filtering before distance ranking. HNSW is
    # present for future partitioned scaling; exact filtered ranking cannot lose hits to other tenants.
    rows = db.execute(
        text("""
        with eligible as materialized (
          select c.*, v.title from kb_chunks c
          join kb_articles a on a.business_id=c.business_id and a.id=c.article_id
          join kb_article_versions v on v.business_id=c.business_id and v.article_id=c.article_id
            and v.version_no=c.version_no
          where c.business_id=:business and a.business_id=:business and v.business_id=:business
            and a.status='published' and a.visibility='public' and a.published_version=c.version_no
            and v.published_at is not null and c.model_id=:model
        ) select article_id, version_no, title, start_offset, end_offset, text,
            embedding <=> :vector as distance from eligible
          where embedding <=> :vector <= 0.8
          order by distance, article_id, chunk_index limit 30
    """).bindparams(bindparam("vector", type_=Vector())),
        {"business": ctx.business_id, "model": embedded.model_id, "vector": embedded.vectors[0]},
    )
    semantic: list[SearchHit] = []
    seen = set()
    for row in rows:
        key = (row.article_id, row.version_no)
        if key in seen:
            continue
        seen.add(key)
        passage = row.text[:700]
        semantic.append(
            SearchHit(
                article_id=row.article_id,
                version=row.version_no,
                title=row.title,
                passages=[passage],
                spans=[SearchSpan(start=row.start_offset, end=row.start_offset + len(passage))],
            )
        )
    scores: dict[tuple[str, int], float] = {}
    hits: dict[tuple[str, int], SearchHit] = {}
    for source in (lexical, semantic):
        for rank, hit in enumerate(source, 1):
            key = (str(hit.article_id), hit.version)
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            hits.setdefault(key, hit)
    keys = sorted(scores, key=lambda k: (-scores[k], k))[: max(1, min(limit, 10))]
    return [hits[k] for k in keys]
