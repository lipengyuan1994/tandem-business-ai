"""Accounting connections and the record of what was pushed to the bookkeeping system.

One connection per business (QuickBooks Online first). OAuth tokens are stored sealed (see crypto.py), never in
plain text, and never leave this module. `accounting_sync_records` maps one Tandem record to its QuickBooks object
for one QuickBooks company (realm), so reconnecting the same company never creates duplicates.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args

CONNECTION_STATUSES = ("pending", "connected", "needs_reconnect", "disconnected")
ENTITY_TYPES = (
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
    "payout_transfer",
    "payout_fees",
)
SYNC_STATUSES = ("synced", "failed", "skipped")


class AccountingConnection(BusinessScopedBase):
    __tablename__ = "accounting_connections"
    provider: Mapped[str] = mapped_column(String(20), default="quickbooks")
    environment: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    realm_id: Mapped[str | None] = mapped_column(String(40))
    company_name: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(8))
    home_currency: Mapped[str | None] = mapped_column(String(3))
    access_token_sealed: Mapped[str | None] = mapped_column(Text)
    refresh_token_sealed: Mapped[str | None] = mapped_column(Text)
    access_expires_at: Mapped[datetime | None]
    refresh_expires_at: Mapped[datetime | None]
    oauth_state_hash: Mapped[str | None] = mapped_column(String(64))
    oauth_state_expires_at: Mapped[datetime | None]
    connected_by: Mapped[uuid.UUID | None]
    connected_at: Mapped[datetime | None]
    # Only records dated on or after this day are pushed (older ones only when a newer record needs them).
    sync_from: Mapped[date | None]
    income_account_id: Mapped[str | None] = mapped_column(String(40))
    receivable_account_id: Mapped[str | None] = mapped_column(String(40))
    deposit_account_id: Mapped[str | None] = mapped_column(String(40))
    tax_account_id: Mapped[str | None] = mapped_column(String(40))
    expense_account_id: Mapped[str | None] = mapped_column(String(40))
    payable_account_id: Mapped[str | None] = mapped_column(String(40))
    bank_account_id: Mapped[str | None] = mapped_column(String(40))
    # Stripe (M21 phase 4): card payments land in a clearing account; payouts move them to the bank, less fees.
    stripe_clearing_account_id: Mapped[str | None] = mapped_column(String(40))
    stripe_fees_account_id: Mapped[str | None] = mapped_column(String(40))
    auto_sync: Mapped[bool] = mapped_column(default=True, server_default="true")
    sync_lease_until: Mapped[datetime | None]
    last_sync_started_at: Mapped[datetime | None]
    last_sync_finished_at: Mapped[datetime | None]
    last_sync_error: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = scoped_table_args(
        UniqueConstraint("business_id"),
        CheckConstraint("provider in ('quickbooks')", name="provider"),
        CheckConstraint("environment in ('sandbox','production')", name="environment"),
        CheckConstraint(f"status in ({','.join(repr(s) for s in CONNECTION_STATUSES)})", name="status"),
    )


class AccountingSyncRecord(BusinessScopedBase):
    __tablename__ = "accounting_sync_records"
    provider: Mapped[str] = mapped_column(String(20), default="quickbooks")
    realm_id: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(24))
    # Tandem id of the record (customer, invoice, ...). For items: a fixed key such as "sales".
    local_id: Mapped[str] = mapped_column(String(64))
    local_version: Mapped[int | None] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200))
    remote_id: Mapped[str | None] = mapped_column(String(40))
    remote_sync_token: Mapped[str | None] = mapped_column(String(20))
    remote_state: Mapped[str | None] = mapped_column(String(12))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_attempt_at: Mapped[datetime | None]
    next_attempt_at: Mapped[datetime | None]
    synced_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(
        UniqueConstraint("business_id", "provider", "realm_id", "entity_type", "local_id"),
        CheckConstraint(f"entity_type in ({','.join(repr(e) for e in ENTITY_TYPES)})", name="entity_type"),
        CheckConstraint(f"status in ({','.join(repr(s) for s in SYNC_STATUSES)})", name="status"),
        CheckConstraint("remote_state is null or remote_state in ('active','voided','deleted')", name="remote_state"),
        Index("ix_accounting_sync_records_status", "business_id", "realm_id", "status"),
    )
