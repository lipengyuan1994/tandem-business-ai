"""Staff endpoints for the QuickBooks Online connection, account mapping and sync log."""

from __future__ import annotations

import urllib.parse
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.platform import clock
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.crud import page, require_scoped
from app.platform.db import transaction
from app.platform.errors import Conflict, DomainError, error_responses
from app.platform.pagination import Page
from app.platform.permissions import require

from . import connection, sync
from . import permissions as P
from .models import AccountingSyncRecord
from .schemas import (
    AccountOut,
    ConnectionOut,
    ConnectStartOut,
    EntityType,
    SettingsIn,
    SyncQueuedOut,
    SyncRecordOut,
    SyncStatus,
    VersionIn,
)

router = APIRouter()
ERRORS = error_responses(400, 401, 403, 404, 409, 422)
SETTINGS_PAGE = "/settings/accounting"


@router.get(
    "/quickbooks", response_model=ConnectionOut, summary="QuickBooks connection and sync status", responses=ERRORS
)
def status(ctx: Context = Depends(require(P.SYNC_READ))) -> ConnectionOut:
    with transaction(ctx) as db:
        return connection.out(db, ctx)


@router.post(
    "/quickbooks/connect",
    response_model=ConnectStartOut,
    summary="Start connecting QuickBooks Online (returns Intuit's sign-in URL)",
    responses=ERRORS,
)
def connect(ctx: Context = Depends(require(P.CONNECTION_MANAGE))) -> ConnectStartOut:
    with transaction(ctx) as db:
        return connection.start(db, ctx)


@router.get("/quickbooks/callback", include_in_schema=False)
def callback(
    code: str = "",
    state: str = "",
    realm_id: str = Query(default="", alias="realmId"),
    error: str = "",
    ctx: Context = Depends(require(P.CONNECTION_MANAGE)),
) -> RedirectResponse:
    """Intuit sends the browser here after sign-in; always answers with a redirect to the settings page."""
    result: dict[str, str] = {"quickbooks": "connected"}
    try:
        if error:
            raise Conflict("accounting.oauth_declined", detail="QuickBooks sign-in was cancelled or refused")
        connection.finish(ctx, code=code, state=state, realm_id=realm_id)
        if ctx.has(P.SYNC_RUN):
            sync.queue_now(ctx)
    except DomainError as exc:
        result = {"quickbooks": "error", "reason": exc.code}
    base = get_settings().app_base_url.rstrip("/")
    return RedirectResponse(f"{base}{SETTINGS_PAGE}?{urllib.parse.urlencode(result)}", status_code=303)


@router.put(
    "/quickbooks/settings",
    response_model=ConnectionOut,
    summary="Set the QuickBooks account mapping, sync start date and automatic sync",
    responses=ERRORS,
)
def update_settings(body: SettingsIn, ctx: Context = Depends(require(P.CONNECTION_MANAGE))) -> ConnectionOut:
    with transaction(ctx) as db:
        return connection.update_settings(db, ctx, body)


@router.get(
    "/quickbooks/accounts",
    response_model=list[AccountOut],
    summary="Active accounts in the connected QuickBooks company",
    responses=ERRORS,
)
def accounts(ctx: Context = Depends(require(P.CONNECTION_MANAGE))) -> list[AccountOut]:
    return connection.accounts(ctx)


@router.post(
    "/quickbooks/disconnect",
    response_model=ConnectionOut,
    summary="Disconnect QuickBooks (records already sent stay there)",
    responses=ERRORS,
)
def disconnect(body: VersionIn, ctx: Context = Depends(require(P.CONNECTION_MANAGE))) -> ConnectionOut:
    connection.disconnect(ctx, body.version)
    with transaction(ctx) as db:
        return connection.out(db, ctx)


@router.post(
    "/quickbooks/sync",
    response_model=SyncQueuedOut,
    status_code=202,
    summary="Sync with QuickBooks now",
    responses=ERRORS,
)
def sync_now(ctx: Context = Depends(require(P.SYNC_RUN))) -> SyncQueuedOut:
    return SyncQueuedOut(queued=sync.queue_now(ctx))


def _record_out(row: AccountingSyncRecord) -> SyncRecordOut:
    return SyncRecordOut.model_validate(row, from_attributes=True)


@router.get(
    "/quickbooks/records",
    response_model=Page[SyncRecordOut],
    summary="Sync log: what was sent to QuickBooks, failed or skipped",
    responses=ERRORS,
)
def records(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    status: list[SyncStatus] | None = Query(default=None),
    entity_type: list[EntityType] | None = Query(default=None),
    ctx: Context = Depends(require(P.SYNC_READ)),
) -> Page[SyncRecordOut]:
    with transaction(ctx) as db:
        conn = connection.get(db, ctx)
        if conn is None or conn.realm_id is None:
            return Page(items=[])
        stmt = select(AccountingSyncRecord).where(
            AccountingSyncRecord.business_id == ctx.business_id,
            AccountingSyncRecord.realm_id == conn.realm_id,
            AccountingSyncRecord.entity_type != "item",
        )
        if status:
            stmt = stmt.where(AccountingSyncRecord.status.in_(status))
        if entity_type:
            stmt = stmt.where(AccountingSyncRecord.entity_type.in_(entity_type))
        rows, next_cursor = page(db, stmt, AccountingSyncRecord, limit, cursor)
        return Page(items=[_record_out(row) for row in rows], next_cursor=next_cursor)


@router.post(
    "/quickbooks/records/{record_id}/retry",
    response_model=SyncRecordOut,
    summary="Try a failed or skipped record again on the next sync (which starts now)",
    responses=ERRORS,
)
def retry(record_id: uuid.UUID, ctx: Context = Depends(require(P.SYNC_RUN))) -> Any:
    with transaction(ctx) as db:
        row = require_scoped(db, ctx, AccountingSyncRecord, record_id, "accounting.record_not_found", for_update=True)
        if row.status == "synced":
            raise Conflict("accounting.record_already_synced", detail="This record is already in QuickBooks")
        row.next_attempt_at = clock.now()
        row.local_version = None  # makes the record due whatever its Tandem version
        row.version += 1
        db.flush()
        out = _record_out(row)
    sync.queue_now(ctx)
    return out
