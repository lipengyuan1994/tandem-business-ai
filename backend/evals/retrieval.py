"""Citation-scored semantic retrieval, through application indexing and search. Live calls are reviewer-owned."""

from typing import Any

from app.modules.knowledge import indexing, repository, service
from app.modules.knowledge.schemas import ArticleCreate
from app.platform.context import Context
from app.platform.db import transaction


def evaluate(ctx: Context, payload: dict[str, Any]) -> dict[str, Any]:
    article_data = ArticleCreate.model_validate(payload["article"])
    with transaction(ctx) as db:
        article = service.create(db, ctx, article_data)
        service.publish(db, ctx, article.id, article.version)
        ident, version = article.id, article.published_version
    indexing.index_article(ctx, {"article_id": str(ident)})
    with transaction(ctx) as db:
        lexical = repository.search(db, ctx, payload["message"], 10)
        hits = service.search_public(db, ctx, payload["message"], 3)
    return {
        "cited_slug": article_data.slug if hits and hits[0].article_id == ident else None,
        "cited_version": hits[0].version if hits else None,
        "fts_missed": not any(h.article_id == ident for h in lexical),
        "citation_correct": bool(hits and hits[0].article_id == ident and hits[0].version == version),
    }
