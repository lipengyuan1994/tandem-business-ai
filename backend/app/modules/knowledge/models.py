from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Computed, ForeignKeyConstraint, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args

SEARCH_EXPRESSION = "setweight(to_tsvector('simple', title), 'A') || to_tsvector('simple', body_markdown)"


class Article(BusinessScopedBase):
    __tablename__ = "kb_articles"
    title: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100))
    visibility: Mapped[str] = mapped_column(String(10), default="public")
    status: Mapped[str] = mapped_column(String(12), default="draft")
    current_version: Mapped[int] = mapped_column(default=1)
    published_version: Mapped[int | None]
    created_by: Mapped[uuid.UUID | None]
    __table_args__ = scoped_table_args(
        UniqueConstraint("business_id", "slug"),
        CheckConstraint("visibility in ('public','internal')", name="visibility"),
        CheckConstraint("status in ('draft','published','archived')", name="status"),
        CheckConstraint(
            "current_version > 0 and (published_version is null or "
            "(published_version > 0 and published_version <= current_version))",
            name="revision",
        ),
        CheckConstraint("status != 'published' or published_version is not null", name="publication"),
    )


class ArticleVersion(BusinessScopedBase):
    __tablename__ = "kb_article_versions"
    article_id: Mapped[uuid.UUID]
    version_no: Mapped[int]
    title: Mapped[str] = mapped_column(String(200))
    body_markdown: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    author: Mapped[uuid.UUID | None]
    published_at: Mapped[datetime | None]
    search_vector: Mapped[str] = mapped_column(TSVECTOR, Computed(SEARCH_EXPRESSION, persisted=True))
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "article_id"], ["kb_articles.business_id", "kb_articles.id"]),
        UniqueConstraint("business_id", "article_id", "version_no"),
        CheckConstraint("version_no > 0 and length(body_markdown) <= 20000", name="body"),
        Index(
            "ix_kb_published_search",
            "search_vector",
            postgresql_using="gin",
            postgresql_where=text("published_at is not null"),
        ),
    )


class Suggestion(BusinessScopedBase):
    __tablename__ = "kb_suggestions"
    question_text: Mapped[str] = mapped_column(Text)
    conversation_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(String(12), default="open")
    article_id: Mapped[uuid.UUID | None]
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "conversation_id"], ["conversations.business_id", "conversations.id"]),
        ForeignKeyConstraint(["business_id", "article_id"], ["kb_articles.business_id", "kb_articles.id"]),
        CheckConstraint("status in ('open','done','dismissed')", name="status"),
        CheckConstraint("length(question_text) <= 4000", name="question"),
        Index(
            "ix_kb_suggestions_open",
            "business_id",
            "conversation_id",
            text("md5(question_text)"),
            unique=True,
            postgresql_where=text("status='open'"),
        ),
    )
