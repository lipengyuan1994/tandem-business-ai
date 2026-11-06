from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require

from . import permissions as P
from . import service
from .models import Article
from .schemas import (
    ArticleCreate,
    ArticleDetail,
    ArticleEdit,
    ArticleOut,
    ConvertIn,
    SuggestionOut,
    VersionIn,
    VersionOut,
)

router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


def _detail(db: Session, ctx: Context, a: Article) -> ArticleDetail:
    return ArticleDetail(
        **ArticleOut.model_validate(a).model_dump(),
        versions=[VersionOut.model_validate(v) for v in service.get_versions(db, ctx, a.id)],
    )


@router.get("/articles", response_model=list[ArticleOut], responses=ERRORS)
def articles(
    query: str | None = Query(default=None, max_length=500),
    status: Literal["draft", "published", "archived"] | None = None,
    visibility: Literal["public", "internal"] | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    ctx: Context = Depends(require(P.READ)),
) -> list[ArticleOut]:
    with transaction(ctx) as db:
        return [
            ArticleOut.model_validate(a)
            for a in service.list_articles(db, ctx, query=query, status=status, visibility=visibility, limit=limit)
        ]


@router.post("/articles", response_model=ArticleDetail, status_code=201, responses=ERRORS)
def create(body: ArticleCreate, ctx: Context = Depends(require(P.WRITE))) -> ArticleDetail:
    with transaction(ctx) as db:
        return _detail(db, ctx, service.create(db, ctx, body))


@router.get("/articles/{article_id}", response_model=ArticleDetail, responses=ERRORS)
def get(article_id: uuid.UUID, ctx: Context = Depends(require(P.READ))) -> ArticleDetail:
    with transaction(ctx) as db:
        return _detail(db, ctx, service.get(db, ctx, article_id))


@router.patch("/articles/{article_id}", response_model=ArticleDetail, responses=ERRORS)
def edit(article_id: uuid.UUID, body: ArticleEdit, ctx: Context = Depends(require(P.WRITE))) -> ArticleDetail:
    with transaction(ctx) as db:
        return _detail(db, ctx, service.edit(db, ctx, article_id, body))


@router.post("/articles/{article_id}/publish", response_model=ArticleDetail, responses=ERRORS)
def publish(article_id: uuid.UUID, body: VersionIn, ctx: Context = Depends(require(P.WRITE))) -> ArticleDetail:
    with transaction(ctx) as db:
        return _detail(db, ctx, service.publish(db, ctx, article_id, body.version))


@router.post("/articles/{article_id}/archive", response_model=ArticleDetail, responses=ERRORS)
def archive(article_id: uuid.UUID, body: VersionIn, ctx: Context = Depends(require(P.WRITE))) -> ArticleDetail:
    with transaction(ctx) as db:
        return _detail(db, ctx, service.archive(db, ctx, article_id, body.version))


@router.get("/suggestions", response_model=list[SuggestionOut], responses=ERRORS)
def suggestions(
    status: Literal["open", "done", "dismissed"] = "open",
    limit: int = Query(default=50, ge=1, le=100),
    ctx: Context = Depends(require(P.READ)),
) -> list[SuggestionOut]:
    with transaction(ctx) as db:
        return [SuggestionOut.model_validate(s) for s in service.list_suggestions(db, ctx, status, limit)]


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=SuggestionOut, responses=ERRORS)
def dismiss(suggestion_id: uuid.UUID, body: VersionIn, ctx: Context = Depends(require(P.WRITE))) -> SuggestionOut:
    with transaction(ctx) as db:
        return SuggestionOut.model_validate(service.dismiss(db, ctx, suggestion_id, body.version))


@router.post("/suggestions/{suggestion_id}/convert", response_model=ArticleDetail, responses=ERRORS)
def convert(suggestion_id: uuid.UUID, body: ConvertIn, ctx: Context = Depends(require(P.WRITE))) -> ArticleDetail:
    with transaction(ctx) as db:
        return _detail(db, ctx, service.convert(db, ctx, suggestion_id, body.version, body.article))
