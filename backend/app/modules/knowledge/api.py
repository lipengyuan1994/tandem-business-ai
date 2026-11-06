from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require
from app.platform.registry import get_registry

from . import indexing, pdf_import, repository, service
from . import permissions as P
from .models import Article
from .pdf_upload import read_pdf
from .schemas import (
    ArticleCreate,
    ArticleDetail,
    ArticleEdit,
    ArticleOut,
    ConvertIn,
    ImportOut,
    ReindexOut,
    SuggestionOut,
    VersionIn,
    VersionOut,
)

router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


def _import(ctx: Context, data: bytes, filename: str) -> ImportOut:
    pages = pdf_import.extract(data)
    with transaction(ctx) as db:
        return ImportOut(drafts=[_detail(db, ctx, a) for a in pdf_import.create_drafts(db, ctx, pages, filename)])


@router.post(
    "/imports",
    response_model=ImportOut,
    status_code=201,
    responses={**error_responses(401, 403, 422), 413: {"description": "PDF exceeds 10 MiB"}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def import_pdf(request: Request, ctx: Context = Depends(require(P.WRITE))) -> ImportOut:
    data, name = await read_pdf(request)
    return await run_in_threadpool(_import, ctx, data, name)


@router.post("/reindex", response_model=ReindexOut, status_code=202, responses=ERRORS)
def reindex(ctx: Context = Depends(require(P.WRITE))) -> ReindexOut:
    with transaction(ctx) as db:
        return ReindexOut(job_id=indexing.reindex(db, ctx))


def _detail(db: Session, ctx: Context, a: Article) -> ArticleDetail:
    return ArticleDetail(
        **_out(ctx, a).model_dump(),
        versions=[VersionOut.model_validate(v) for v in repository.versions(db, ctx, a.id)],
    )


def _out(ctx: Context, a: Article, *, model: str | None = None, checked: bool = False) -> ArticleOut:
    value = ArticleOut.model_validate(a)
    if a.status == "published" and a.visibility == "public":
        if not checked:
            provider = get_registry().knowledge_embeddings
            model = provider.model(ctx) if provider else None
        if model is None:
            value.search_index_state = "unavailable"
            value.search_index_error = "knowledge.embedding_unavailable"
        elif a.search_index_state == "indexed" and a.search_index_model != model:
            value.search_index_state = "pending"
    return value


@router.get("/articles", response_model=list[ArticleOut], responses=ERRORS)
def articles(
    query: str | None = Query(default=None, max_length=500),
    status: Literal["draft", "published", "archived"] | None = None,
    visibility: Literal["public", "internal"] | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    ctx: Context = Depends(require(P.READ)),
) -> list[ArticleOut]:
    provider = get_registry().knowledge_embeddings
    model = provider.model(ctx) if provider else None
    with transaction(ctx) as db:
        return [
            _out(ctx, a, model=model, checked=True)
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


@router.post("/articles/{article_id}/unpublish", response_model=ArticleDetail, responses=ERRORS)
def unpublish(article_id: uuid.UUID, body: VersionIn, ctx: Context = Depends(require(P.WRITE))) -> ArticleDetail:
    with transaction(ctx) as db:
        return _detail(db, ctx, service.unpublish(db, ctx, article_id, body.version))


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
