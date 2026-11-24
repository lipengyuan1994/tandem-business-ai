"""Connecting a business to QuickBooks Online: OAuth start and callback, tokens, settings, disconnect.

Network calls never run while a request holds row locks longer than needed: the callback checks and clears the
one-time state in one transaction, talks to Intuit outside any transaction, then stores the result in another.
Token refresh is the exception: it runs under the connection's row lock so two workers never spend the same
(single-use, rotating) refresh token.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.org.public import get_business_brief
from app.platform import audit, clock
from app.platform.config import is_regulated
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict, Invalid

from . import permissions as P
from . import provider
from .crypto import UnsealError, seal, unseal
from .models import AccountingConnection, AccountingSyncRecord
from .quickbooks import LedgerApi, LedgerError, Tokens, quote_literal
from .schemas import AccountMapping, AccountOut, ConnectionOut, ConnectStartOut, SettingsIn, SyncCounts

STATE_TTL = timedelta(minutes=10)
REFRESH_MARGIN = timedelta(minutes=5)
MAPPING_FIELDS = tuple(AccountMapping.model_fields)
REQUIRED_MAPPING = ("income_account_id", "expense_account_id", "bank_account_id")
REALM_ID = re.compile(r"^[0-9]{1,40}$")


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What a sync run needs from the connection, read once at its start."""

    realm_id: str
    environment: str
    country: str | None
    home_currency: str | None
    sync_from: date
    mapping: AccountMapping


def get(db: Session, ctx: Context, *, lock: bool = False) -> AccountingConnection | None:
    stmt = select(AccountingConnection).where(AccountingConnection.business_id == ctx.business_id)
    return db.scalar(stmt.with_for_update() if lock else stmt)


def _bump(conn: AccountingConnection) -> None:
    conn.version += 1


def _mapping(conn: AccountingConnection) -> AccountMapping:
    return AccountMapping(**{name: getattr(conn, name) for name in MAPPING_FIELDS})


def missing_mapping(conn: AccountingConnection) -> list[str]:
    return [name for name in REQUIRED_MAPPING if not getattr(conn, name)]


def ledger_failure(exc: LedgerError) -> Conflict:
    return Conflict(f"accounting.quickbooks_{exc.kind}", detail=exc.message)


def out(db: Session, ctx: Context) -> ConnectionOut:
    ctx.require(P.SYNC_READ)
    business = get_business_brief(db, ctx)
    conn = get(db, ctx)
    if conn is None:
        return ConnectionOut(
            status="not_connected",
            configured=provider.configured(),
            environment=provider.environment(),
            business_currency=business.currency,
        )
    counts = SyncCounts()
    if conn.realm_id:
        rows = db.execute(
            select(AccountingSyncRecord.status, func.count())
            .where(
                AccountingSyncRecord.business_id == ctx.business_id,
                AccountingSyncRecord.realm_id == conn.realm_id,
                AccountingSyncRecord.entity_type != "item",
            )
            .group_by(AccountingSyncRecord.status)
        )
        counts = SyncCounts(**{status: count for status, count in rows})
    return ConnectionOut(
        configured=provider.configured(),
        status=conn.status,
        environment=conn.environment,
        business_currency=business.currency,
        company_name=conn.company_name,
        country=conn.country,
        home_currency=conn.home_currency,
        connected_at=conn.connected_at,
        sync_from=conn.sync_from,
        auto_sync=conn.auto_sync,
        mapping=_mapping(conn),
        missing_mapping=missing_mapping(conn),
        last_sync_started_at=conn.last_sync_started_at,
        last_sync_finished_at=conn.last_sync_finished_at,
        last_sync_error=conn.last_sync_error,
        counts=counts,
        version=conn.version,
    )


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def start(db: Session, ctx: Context) -> ConnectStartOut:
    """Begin the Intuit sign-in: store a one-time state and return the URL to send the browser to."""
    ctx.require(P.CONNECTION_MANAGE)
    if is_regulated():
        raise Conflict(
            "accounting.regulated_profile",
            detail="QuickBooks sync is off for the regulated data profile (customer names would leave this server)",
        )
    if not provider.configured():
        raise Conflict("accounting.not_configured", detail="Set QUICKBOOKS_CLIENT_ID and QUICKBOOKS_CLIENT_SECRET")
    state = secrets.token_urlsafe(32)
    conn = get(db, ctx, lock=True)
    if conn is None:
        conn = AccountingConnection(
            business_id=ctx.business_id, provider="quickbooks", environment=provider.environment(), status="pending"
        )
        db.add(conn)
    else:
        if conn.status == "disconnected":
            conn.status = "pending"
        _bump(conn)
    conn.oauth_state_hash = _state_hash(state)
    conn.oauth_state_expires_at = clock.now() + STATE_TTL
    db.flush()
    audit.record(
        db,
        ctx,
        action="accounting.connect_started",
        target_type="accounting_connection",
        target_id=conn.id,
        summary="Started connecting QuickBooks Online",
    )
    with provider.http_client() as client:
        return ConnectStartOut(authorize_url=provider.auth(client).authorize_url(state))


def _store_tokens(conn: AccountingConnection, tokens: Tokens) -> None:
    conn.access_token_sealed = seal(tokens.access_token)
    conn.refresh_token_sealed = seal(tokens.refresh_token)
    conn.access_expires_at = tokens.access_expires_at
    conn.refresh_expires_at = tokens.refresh_expires_at


def finish(ctx: Context, *, code: str, state: str, realm_id: str) -> None:
    """Intuit redirected back: check the one-time state, exchange the code, read the company, store tokens."""
    ctx.require(P.CONNECTION_MANAGE)
    if not code or not REALM_ID.match(realm_id or ""):
        raise Invalid("accounting.oauth_response_invalid", detail="QuickBooks did not return a code and company id")
    with transaction(ctx) as db:
        conn = get(db, ctx, lock=True)
        expected = conn.oauth_state_hash if conn else None
        expires = conn.oauth_state_expires_at if conn else None
        if conn is None or expected is None or expires is None:
            raise Invalid("accounting.oauth_state_invalid", detail="This QuickBooks sign-in was not started here")
        conn.oauth_state_hash = None
        conn.oauth_state_expires_at = None
        _bump(conn)
        valid = hmac.compare_digest(expected, _state_hash(state or "")) and expires > clock.now()
    if not valid:
        raise Invalid("accounting.oauth_state_invalid", detail="This QuickBooks sign-in expired or was already used")
    env = provider.environment()
    try:
        with provider.http_client() as client:
            tokens = provider.auth(client).exchange_code(code)
            company = provider.api(client, env, realm_id, tokens.access_token).company()
    except LedgerError as exc:
        raise ledger_failure(exc) from exc
    with transaction(ctx) as db:
        business = get_business_brief(db, ctx)
        conn = get(db, ctx, lock=True)
        assert conn is not None
        if conn.realm_id is not None and conn.realm_id != realm_id:
            for name in MAPPING_FIELDS:  # account ids belong to the previous company
                setattr(conn, name, None)
        _store_tokens(conn, tokens)
        conn.environment = env
        conn.realm_id = realm_id
        conn.status = "connected"
        conn.company_name = (company.get("company_name") or "")[:200] or None
        conn.country = (company.get("country") or "")[:8] or None
        conn.home_currency = (company.get("home_currency") or "")[:3] or None
        conn.connected_by = ctx.user_id
        conn.connected_at = clock.now()
        conn.last_sync_error = None
        if conn.sync_from is None:
            conn.sync_from = clock.now().astimezone(ZoneInfo(business.timezone)).date()
        _bump(conn)
        audit.record(
            db,
            ctx,
            action="accounting.connected",
            target_type="accounting_connection",
            target_id=conn.id,
            summary=f"Connected QuickBooks Online company {conn.company_name or realm_id} ({env})",
        )


def update_settings(db: Session, ctx: Context, body: SettingsIn) -> ConnectionOut:
    ctx.require(P.CONNECTION_MANAGE)
    conn = get(db, ctx, lock=True)
    if conn is None or conn.realm_id is None:
        raise Conflict("accounting.not_connected", detail="Connect QuickBooks first")
    if conn.version != body.version:
        raise Conflict("accounting.version_conflict", current_version=conn.version)
    before = {name: getattr(conn, name) for name in (*MAPPING_FIELDS, "sync_from", "auto_sync")}
    for name in MAPPING_FIELDS:
        setattr(conn, name, getattr(body, name))
    conn.sync_from = body.sync_from
    conn.auto_sync = body.auto_sync
    _bump(conn)
    audit.record(
        db,
        ctx,
        action="accounting.settings_updated",
        target_type="accounting_connection",
        target_id=conn.id,
        summary="Updated QuickBooks account mapping and sync settings",
        changes=audit.diff(before, conn, before.keys()),
    )
    db.flush()
    return out(db, ctx)


def disconnect(ctx: Context, version: int) -> None:
    """Revoke at Intuit (best effort) and forget the tokens. What was already pushed stays in QuickBooks."""
    ctx.require(P.CONNECTION_MANAGE)
    with transaction(ctx) as db:
        conn = get(db, ctx, lock=True)
        if conn is None or conn.status == "disconnected":
            raise Conflict("accounting.not_connected", detail="QuickBooks is not connected")
        if conn.version != version:
            raise Conflict("accounting.version_conflict", current_version=conn.version)
        sealed = conn.refresh_token_sealed
        _clear_tokens(conn)
        conn.status = "disconnected"
        conn.sync_lease_until = None
        _bump(conn)
        audit.record(
            db,
            ctx,
            action="accounting.disconnected",
            target_type="accounting_connection",
            target_id=conn.id,
            summary=f"Disconnected QuickBooks Online company {conn.company_name or conn.realm_id}",
        )
    if sealed and provider.configured():
        try:
            with provider.http_client() as client:
                provider.auth(client).revoke(unseal(sealed))
        except (LedgerError, UnsealError):
            pass  # tokens are already gone here; an unrevoked refresh token expires on Intuit's side


def _clear_tokens(conn: AccountingConnection) -> None:
    conn.access_token_sealed = None
    conn.refresh_token_sealed = None
    conn.access_expires_at = None
    conn.refresh_expires_at = None


def mark_needs_reconnect(ctx: Context, message: str) -> None:
    with transaction(ctx) as db:
        conn = get(db, ctx, lock=True)
        if conn is not None and conn.status == "connected":
            _clear_tokens(conn)
            conn.status = "needs_reconnect"
            conn.last_sync_error = message[:500]
            conn.sync_lease_until = None
            _bump(conn)


def _access(ctx: Context, client: object, *, force_refresh: bool) -> tuple[str, Snapshot]:
    """A usable access token, refreshed under the row lock when it is about to expire."""
    failure: LedgerError | None = None
    access = ""
    with transaction(ctx) as db:
        conn = get(db, ctx, lock=True)
        if conn is None or conn.status != "connected" or conn.realm_id is None:
            raise Conflict("accounting.not_connected", detail="QuickBooks is not connected")
        snapshot = Snapshot(
            realm_id=conn.realm_id,
            environment=conn.environment,
            country=conn.country,
            home_currency=conn.home_currency,
            sync_from=conn.sync_from or clock.now().date(),
            mapping=_mapping(conn),
        )
        expiring = conn.access_expires_at is None or conn.access_expires_at - clock.now() < REFRESH_MARGIN
        try:
            if conn.environment != provider.environment():
                raise LedgerError("auth", "The server now points at a different QuickBooks environment; reconnect")
            if force_refresh or expiring or not conn.access_token_sealed:
                if not conn.refresh_token_sealed:
                    raise LedgerError("auth", "No QuickBooks sign-in is stored; reconnect")
                tokens = provider.auth(client).refresh(unseal(conn.refresh_token_sealed))  # type: ignore[arg-type]
                _store_tokens(conn, tokens)
                _bump(conn)
                access = tokens.access_token
            else:
                access = unseal(conn.access_token_sealed)
        except UnsealError:
            failure = LedgerError("auth", "The stored QuickBooks sign-in can no longer be read; reconnect")
        except LedgerError as exc:
            failure = exc
        if failure is not None and failure.kind == "auth":
            _clear_tokens(conn)
            conn.status = "needs_reconnect"
            conn.last_sync_error = failure.message
            conn.sync_lease_until = None
            _bump(conn)
    if failure is not None:
        raise failure
    return access, snapshot


class LiveSession:
    """A live QuickBooks API handle for one business; `reauthorize()` refreshes the token once after a 401."""

    def __init__(self, ctx: Context, client: object) -> None:
        self._ctx, self._client = ctx, client
        token, self.snapshot = _access(ctx, client, force_refresh=False)
        self.api: LedgerApi = provider.api(client, self.snapshot.environment, self.snapshot.realm_id, token)  # type: ignore[arg-type]
        self._refreshed = False

    def reauthorize(self) -> bool:
        if self._refreshed:
            return False
        self._refreshed = True
        token, self.snapshot = _access(self._ctx, self._client, force_refresh=True)
        self.api = provider.api(self._client, self.snapshot.environment, self.snapshot.realm_id, token)  # type: ignore[arg-type]
        return True


@contextmanager
def session(ctx: Context) -> Iterator[LiveSession]:
    with provider.http_client() as client:
        yield LiveSession(ctx, client)


ACCOUNT_TYPES = (
    "Income",
    "Other Income",
    "Accounts Receivable",
    "Bank",
    "Other Current Asset",
    "Other Current Liability",
    "Expense",
    "Cost of Goods Sold",
    "Other Expense",
    "Accounts Payable",
    "Credit Card",
)


def accounts(ctx: Context) -> list[AccountOut]:
    """Active QuickBooks accounts the mapping can point at."""
    ctx.require(P.CONNECTION_MANAGE)
    types = ", ".join(quote_literal(t) for t in ACCOUNT_TYPES)
    try:
        with session(ctx) as live:
            # Intuit's query language, not SQL; the account types are fixed literals
            rows = live.api.query(
                f"select * from Account where Active = true and AccountType in ({types}) maxresults 1000"  # noqa: S608
            )
    except LedgerError as exc:
        raise ledger_failure(exc) from exc
    found = [
        AccountOut(
            id=str(row["Id"]),
            name=str(row.get("FullyQualifiedName") or row.get("Name") or row["Id"]),
            account_type=str(row.get("AccountType") or ""),
            sub_type=row.get("AccountSubType"),
        )
        for row in rows
        if row.get("Id")
    ]
    return sorted(found, key=lambda a: (a.account_type, a.name.lower()))
