from __future__ import annotations

import re
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.errors import NotFound

from .models import Article, ArticleVersion, Suggestion
from .schemas import SearchHit, SearchSpan


def get(db: Session, ctx: Context, article_id: uuid.UUID, *, lock: bool = False) -> Article:
    stmt = select(Article).where(Article.business_id == ctx.business_id, Article.id == article_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:
        raise NotFound("knowledge.not_found")
    return row


def versions(db: Session, ctx: Context, article_id: uuid.UUID) -> list[ArticleVersion]:
    return list(
        db.scalars(
            select(ArticleVersion)
            .where(ArticleVersion.business_id == ctx.business_id, ArticleVersion.article_id == article_id)
            .order_by(ArticleVersion.version_no)
        )
    )


def articles(
    db: Session, ctx: Context, query: str | None, status: str | None, visibility: str | None, limit: int
) -> list[Article]:
    stmt = select(Article).where(Article.business_id == ctx.business_id)
    if status:
        stmt = stmt.where(Article.status == status)
    if visibility:
        stmt = stmt.where(Article.visibility == visibility)
    if query and query.strip():
        # Staff list includes drafts, so search the latest editable revision here.
        stmt = stmt.join(
            ArticleVersion,
            (ArticleVersion.business_id == Article.business_id)
            & (ArticleVersion.article_id == Article.id)
            & (ArticleVersion.version_no == Article.current_version),
        )
        stmt = stmt.where(ArticleVersion.search_vector.op("@@")(text("plainto_tsquery('simple', :q)"))).params(
            q=query[:500]
        )
    return list(db.scalars(stmt.order_by(Article.updated_at.desc(), Article.id).limit(max(1, min(limit, 100)))))


def search(db: Session, ctx: Context, query: str, limit: int, *, internal: bool = False) -> list[SearchHit]:
    if not query.strip():
        return []
    rows = db.execute(
        text("""
        select a.id, v.version_no, v.title, v.body_markdown
        from kb_articles a join kb_article_versions v
          on v.business_id=a.business_id and v.article_id=a.id and v.version_no=a.published_version
        cross join plainto_tsquery('simple', :query) q(query)
        where a.business_id=:business and v.business_id=:business and a.status='published'
          and v.published_at is not null and (a.visibility='public' or :internal)
          and v.search_vector @@ q.query
        order by ts_rank_cd(v.search_vector, q.query, 32) desc, a.id
        limit :limit
    """),
        {"business": ctx.business_id, "query": query[:500], "internal": internal, "limit": max(1, min(limit, 10))},
    )
    hits = []
    terms = re.findall(r"\w+", query[:500])
    for row in rows:
        match = re.search("|".join(re.escape(t) for t in terms), row.body_markdown, re.I) if terms else None
        start = max(0, match.start() - 100) if match else 0
        passage = row.body_markdown[start : start + 700]
        hits.append(
            SearchHit(
                article_id=row.id,
                version=row.version_no,
                title=row.title,
                passages=[passage],
                spans=[SearchSpan(start=start, end=start + len(passage))],
            )
        )
    return hits


def suggestions(db: Session, ctx: Context, status: str, limit: int) -> list[Suggestion]:
    return list(
        db.scalars(
            select(Suggestion)
            .where(Suggestion.business_id == ctx.business_id, Suggestion.status == status)
            .order_by(Suggestion.created_at.desc(), Suggestion.id.desc())
            .limit(max(1, min(limit, 100)))
        )
    )


def suggestion(db: Session, ctx: Context, suggestion_id: uuid.UUID) -> Suggestion:
    row = db.scalar(
        select(Suggestion)
        .where(Suggestion.business_id == ctx.business_id, Suggestion.id == suggestion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("knowledge.not_found")
    return row
