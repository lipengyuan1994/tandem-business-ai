from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.conversations import public as conversations
from app.platform import audit, clock
from app.platform.context import Context
from app.platform.errors import Conflict

from . import indexing, repository, retrieval
from . import permissions as P
from .models import Article, ArticleVersion, Suggestion
from .schemas import ArticleCreate, ArticleEdit, SearchHit


def _version(actual: int, expected: int) -> None:
    if actual != expected:
        raise Conflict("knowledge.version_conflict", current_version=actual)


def create(db: Session, ctx: Context, data: ArticleCreate) -> Article:
    ctx.require(P.WRITE)
    # An advisory lock gives duplicate slugs a domain error even with concurrent creates.
    from sqlalchemy import text

    db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"kb-slug:{ctx.business_id}:{data.slug}"},
    )
    if db.scalar(select(Article.id).where(Article.business_id == ctx.business_id, Article.slug == data.slug)):
        raise Conflict("knowledge.slug_exists")
    a = Article(
        business_id=ctx.business_id,
        title=data.title,
        slug=data.slug,
        visibility=data.visibility,
        status="draft",
        current_version=1,
        created_by=ctx.user_id,
    )
    db.add(a)
    db.flush()
    db.add(
        ArticleVersion(
            business_id=ctx.business_id,
            article_id=a.id,
            version_no=1,
            title=data.title,
            body_markdown=data.body_markdown,
            tags=data.tags,
            author=ctx.user_id,
        )
    )
    db.flush()
    return a


def get(db: Session, ctx: Context, article_id: uuid.UUID) -> Article:
    ctx.require(P.READ)
    return repository.get(db, ctx, article_id)


def get_versions(db: Session, ctx: Context, article_id: uuid.UUID) -> list[ArticleVersion]:
    get(db, ctx, article_id)
    return repository.versions(db, ctx, article_id)


def list_articles(
    db: Session,
    ctx: Context,
    *,
    query: str | None = None,
    status: str | None = None,
    visibility: str | None = None,
    limit: int = 50,
) -> list[Article]:
    ctx.require(P.READ)
    return repository.articles(db, ctx, query, status, visibility, limit)


def edit(db: Session, ctx: Context, article_id: uuid.UUID, data: ArticleEdit) -> Article:
    ctx.require(P.WRITE)
    a = repository.get(db, ctx, article_id, lock=True)
    _version(a.version, data.version)
    a.current_version += 1
    a.version += 1
    # Visibility applies immediately, including narrowing a previously public article.
    if data.visibility is not None:
        if a.visibility == "internal" and data.visibility == "public" and a.status == "published":
            a.status = "draft"  # Widening access requires explicit publication of the new revision.
        a.visibility = data.visibility
    a.title = data.title
    if a.status != "published" or a.visibility != "public":
        indexing.invalidate(db, ctx, a)
    db.add(
        ArticleVersion(
            business_id=ctx.business_id,
            article_id=a.id,
            version_no=a.current_version,
            title=data.title,
            body_markdown=data.body_markdown,
            tags=data.tags,
            author=ctx.user_id,
        )
    )
    db.flush()
    return a


def publish(db: Session, ctx: Context, article_id: uuid.UUID, version: int) -> Article:
    ctx.require(P.WRITE)
    a = repository.get(db, ctx, article_id, lock=True)
    _version(a.version, version)
    v = repository.versions(db, ctx, a.id)[-1]
    a.current_version += 1
    a.published_version = a.current_version
    a.status = "published"
    a.version += 1
    db.add(
        ArticleVersion(
            business_id=ctx.business_id,
            article_id=a.id,
            version_no=a.current_version,
            title=v.title,
            body_markdown=v.body_markdown,
            tags=v.tags,
            author=ctx.user_id,
            published_at=clock.now(),
        )
    )
    audit.record(
        db,
        ctx,
        action="knowledge.published",
        target_type="kb_article",
        target_id=a.id,
        summary=f"Published article version {a.published_version}",
    )
    db.flush()
    indexing.schedule(db, ctx, a)
    return a


def archive(db: Session, ctx: Context, article_id: uuid.UUID, version: int) -> Article:
    ctx.require(P.WRITE)
    a = repository.get(db, ctx, article_id, lock=True)
    _version(a.version, version)
    a.status = "archived"
    indexing.invalidate(db, ctx, a)
    a.version += 1
    audit.record(
        db,
        ctx,
        action="knowledge.archived",
        target_type="kb_article",
        target_id=a.id,
        summary="Archived knowledge article",
    )
    db.flush()
    return a


def unpublish(db: Session, ctx: Context, article_id: uuid.UUID, version: int) -> Article:
    ctx.require(P.WRITE)
    a = repository.get(db, ctx, article_id, lock=True)
    _version(a.version, version)
    a.status, a.version = "draft", a.version + 1
    indexing.invalidate(db, ctx, a)
    audit.record(
        db,
        ctx,
        action="knowledge.unpublished",
        target_type="kb_article",
        target_id=a.id,
        summary="Unpublished knowledge article",
    )
    db.flush()
    return a


def search_public(db: Session, ctx: Context, query: str, limit: int = 5) -> list[SearchHit]:
    ctx.require(P.READ)
    return retrieval.search(db, ctx, query, limit)


def search_staff(db: Session, ctx: Context, query: str, limit: int = 5) -> list[SearchHit]:
    ctx.require(P.READ)
    return repository.search(db, ctx, query, limit, internal=True)


def suggest(db: Session, ctx: Context, conversation_id: uuid.UUID, question: str) -> None:
    conversations.get_thread(db, ctx, conversation_id, last_n=1)
    question = re.sub(r"[\w.%+'-]+@[\w.-]+\.[A-Za-z]+", "[email hidden]", question)
    question = re.sub(r"(?<!\w)\+?\(?\d[\d\s().-]{7,}\d(?!\w)", "[phone hidden]", question)[:4000]
    db.execute(
        insert(Suggestion)
        .values(business_id=ctx.business_id, conversation_id=conversation_id, question_text=question, status="open")
        .on_conflict_do_nothing()
    )


def list_suggestions(db: Session, ctx: Context, status: str = "open", limit: int = 50) -> list[Suggestion]:
    ctx.require(P.READ)
    return repository.suggestions(db, ctx, status, limit)


def _suggestion(db: Session, ctx: Context, suggestion_id: uuid.UUID, version: int) -> Suggestion:
    ctx.require(P.WRITE)
    s = repository.suggestion(db, ctx, suggestion_id)
    _version(s.version, version)
    if s.status != "open":
        raise Conflict("knowledge.suggestion_closed")
    return s


def dismiss(db: Session, ctx: Context, suggestion_id: uuid.UUID, version: int) -> Suggestion:
    s = _suggestion(db, ctx, suggestion_id, version)
    s.status, s.version = "dismissed", s.version + 1
    db.flush()
    return s


def convert(db: Session, ctx: Context, suggestion_id: uuid.UUID, version: int, data: ArticleCreate) -> Article:
    s = _suggestion(db, ctx, suggestion_id, version)
    a = create(db, ctx, data)
    s.status, s.article_id, s.version = "done", a.id, s.version + 1
    db.flush()
    return a
