from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Tag = Annotated[str, Field(min_length=1, max_length=50)]


class ArticleText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    body_markdown: str = Field(min_length=1, max_length=20000)
    tags: list[Tag] = Field(default_factory=list, max_length=20)


class ArticleCreate(ArticleText):
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    visibility: Literal["public", "internal"] = "public"


class ArticleEdit(ArticleText):
    version: int = Field(ge=1)
    visibility: Literal["public", "internal"] | None = None


class VersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class ConvertIn(VersionIn):
    article: ArticleCreate


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    slug: str
    visibility: str
    status: str
    current_version: int
    published_version: int | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    version: int
    source: str = "manual"
    source_filename: str | None = None
    source_page_start: int | None = None
    source_page_end: int | None = None
    search_index_state: str = "not_applicable"
    search_index_error: str | None = None
    search_index_model: str | None = None


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version_no: int
    title: str
    body_markdown: str
    tags: list[str]
    author: uuid.UUID | None
    created_at: datetime
    published_at: datetime | None


class ArticleDetail(ArticleOut):
    versions: list[VersionOut]


class ImportOut(BaseModel):
    drafts: list[ArticleDetail]


class ReindexOut(BaseModel):
    job_id: uuid.UUID


class SuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    question_text: str
    conversation_id: uuid.UUID
    created_at: datetime
    status: str
    article_id: uuid.UUID | None
    version: int


class SearchSpan(BaseModel):
    start: int
    end: int


class SearchHit(BaseModel):
    article_id: uuid.UUID
    version: int
    title: str
    passages: list[str]
    spans: list[SearchSpan] = Field(default_factory=list)
