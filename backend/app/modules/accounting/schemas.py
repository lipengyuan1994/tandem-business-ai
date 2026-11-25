from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

ConnectionState = Literal["not_connected", "pending", "connected", "needs_reconnect", "disconnected"]
SyncStatus = Literal["synced", "failed", "skipped"]
EntityType = Literal[
    "customer",
    "supplier",
    "invoice",
    "credit_note",
    "credit_application",
    "customer_refund",
    "supplier_credit",
    "invoice_payment",
    "bill",
    "bill_payment",
    "item",
]
AccountId = Field(default=None, pattern=r"^[0-9]{1,20}$")


class AccountMapping(BaseModel):
    """QuickBooks account ids. Income, expense and bank are needed; the rest fall back to QuickBooks defaults."""

    income_account_id: str | None = AccountId
    receivable_account_id: str | None = AccountId
    deposit_account_id: str | None = AccountId
    tax_account_id: str | None = AccountId
    expense_account_id: str | None = AccountId
    payable_account_id: str | None = AccountId
    bank_account_id: str | None = AccountId


class SyncCounts(BaseModel):
    synced: int = 0
    failed: int = 0
    skipped: int = 0


class ConnectionOut(BaseModel):
    provider: Literal["quickbooks"] = "quickbooks"
    configured: bool
    status: ConnectionState
    environment: Literal["sandbox", "production"]
    company_name: str | None = None
    country: str | None = None
    home_currency: str | None = None
    business_currency: str
    connected_at: datetime | None = None
    sync_from: date | None = None
    auto_sync: bool = True
    mapping: AccountMapping = Field(default_factory=AccountMapping)
    missing_mapping: list[str] = Field(default_factory=list)
    last_sync_started_at: datetime | None = None
    last_sync_finished_at: datetime | None = None
    last_sync_error: str | None = None
    counts: SyncCounts = Field(default_factory=SyncCounts)
    version: int | None = None


class ConnectStartOut(BaseModel):
    authorize_url: str


class VersionIn(BaseModel):
    version: int


class SettingsIn(AccountMapping):
    version: int
    sync_from: date
    auto_sync: bool = True


class AccountOut(BaseModel):
    id: str
    name: str
    account_type: str
    sub_type: str | None = None


class SyncQueuedOut(BaseModel):
    queued: bool


class SyncRecordOut(BaseModel):
    id: uuid.UUID
    entity_type: EntityType
    local_id: str
    label: str
    status: SyncStatus
    remote_id: str | None
    remote_state: str | None
    attempts: int
    last_error: str | None
    last_attempt_at: datetime | None
    next_attempt_at: datetime | None
    synced_at: datetime | None
    updated_at: datetime
